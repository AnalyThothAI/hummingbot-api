# CLMM LP Controller 设计（`clmm_lp`）

本文描述当前 `bots/controllers/generic/clmm_lp.py` 版本的设计与边界，按 Hummingbot v2 Controller 实践组织：`update_processed_data()` 负责观测与视图，`determine_executor_actions()` 负责决策与状态推进。

## 1. 目标与原则（先定“改什么，不改什么”）

**不改的部分（保持策略能力与框架契约）**
- 仍然是 **单一 Controller orchestrator**（不拆多个 controller 并行跑，避免互相打架）。
- Controller 只输出 `CreateExecutorAction` / `StopExecutorAction`，不直接下单/改余额。
- 执行仍由 Executors 完成：`LPPositionExecutor`（开/关仓）与 `GatewaySwapExecutor`（swap）。
- 支持 **同一 controller 多个 active executors**，按 `executor_id` 维护独立上下文。

**要改的部分（可读、可维护、契约清晰）**
- 把 token 顺序/反转问题集中到 `TokenOrderMapper`（适配层）。
- 把成本过滤独立成模块：`bots/controllers/generic/clmm_lp_cost_filter.py`。
- Controller 主流程固定为：`snapshot -> reconcile -> decide -> apply_patch -> actions`。
- 日志只保留关键异常/告警，不做大量节流/Debug 采样逻辑（避免性能与维护负担）。

## 2. 文件入口

- Controller：`bots/controllers/generic/clmm_lp.py`
- Types / Adapters / Context：`bots/controllers/generic/clmm_lp_components.py`
- Cost Filter：`bots/controllers/generic/clmm_lp_cost_filter.py`
- 配置示例：`bots/conf/controllers/clmm_lp.yml`
- 官方参考（对比用）：`hummingbot/controllers/generic/lp_manager.py`
- LP Executor：`hummingbot/hummingbot/strategy_v2/executors/lp_position_executor/`
- Swap Executor：`hummingbot/hummingbot/strategy_v2/executors/gateway_swap_executor/`

## 3. 价格与 token 顺序（Uniswap V3 / CLMM 必须搞清楚）

### 3.1 BASE/QUOTE 是“策略定义”，不是池子定义
- `trading_pair`：策略侧 BASE-QUOTE（钱包余额语义、预算计算、router 报价/下单均按这个方向）。
- Uniswap V3 池子链上固定的是 `token0/token1`（地址排序），这会导致“池子 token 顺序”和策略直觉顺序可能相反。

### 3.2 `pool_trading_pair` 的作用
当池子的 token0-token1 顺序与策略侧 `trading_pair` 方向相反时，通过配置：
```yaml
trading_pair: MEMES-USDT
pool_trading_pair: USDT-MEMES
```
Controller 通过 `TokenOrderMapper` 统一做三件事：
- **开仓参数映射**：策略侧的 base/quote 金额与价格区间，映射到 LP executor 所需的 pool token 顺序。
- **executor 上报映射**：把 executor.custom_info 里按 pool 语义上报的 base/quote/price/bounds 映射回策略语义。
- **避免“swap 与加池子方向反了”**：swap 永远用 `trading_pair`；LP 永远用 `pool_trading_pair` + pool token 顺序。

### 3.3 当前实现的价格来源（对齐官方）
`current_price` 统一来自 `MarketDataProvider.get_rate(trading_pair)`（RateOracle / gateway `/price`），与官方 `lp_manager` 与 `LPPositionExecutor._get_current_price()` 一致：
- 不再在 controller 内部通过 `get_pool_info_by_address(pool_address)` 拉“指定 pool 的快照价”。
- 这样可以避免 “router 价 vs pool 价” 双源不一致导致的状态错觉（特别是 out-of-range 判定）。

> 结论：`pool_address` 仍然必须配置（用于 LP 开/关仓），但不再用于 controller 自己抓价格。

## 4. 三层结构（单文件入口 + 内部模块化）

### 4.1 适配层：TokenOrderMapper
位置：`bots/controllers/generic/clmm_lp_components.py`。
- `strategy_amounts_to_lp()` / `strategy_bounds_to_lp()`：开仓时把金额与区间映射到 pool 顺序。
- `lp_amounts_to_strategy()` / `lp_bounds_to_strategy()`：解析 executor 上报时映射回策略顺序。

### 4.2 Portfolio & Budget（观测层）
Controller 维护的钱包与派生数据：
- `wallet_base/wallet_quote`：来自 connector balances（按策略 `trading_pair` 的 token）。
- `BudgetAnchor`：用于 stoploss 的“预算切片锚定值”（按 quote 计价），按 `executor_id` 存在 `ControllerContext.lp[executor_id].anchor`。
- `FeeEstimatorContext`：用于 cost filter 的 fee_rate EWMA（按 `position_address` 绑定）。

### 4.3 Flow（决策层）
三个 flow（都在 `clmm_lp.py` 内，以 `Decision/Intent` 表达）：
- Entry：入场前必要时做 inventory swap，然后开 LP。
- Rebalance：出界 -> stop ->（延时）-> 必要时 inventory swap -> reopen。
- StopLoss：触发后 stop 全部 LP，进入冷却；可选执行 liquidation swap（base->quote）。

## 5. Controller 边界（完全对齐 v2 实践）

### 5.1 `update_processed_data()`：观测与视图
- 允许做 IO：刷新 balances。
- 允许做“观测驱动”的 ctx 更新：reconcile 已完成 swap、更新 anchors、更新 fee EWMA。
- 构建 `Snapshot` 并缓存到 `_latest_snapshot`，同时输出基础 `processed_data`（价格/余额/active executors 列表）。

### 5.2 `determine_executor_actions()`：决策与状态推进
- 读取 `_latest_snapshot`（或当次构建），然后：
  1) reconcile（done swaps、rebalance plans、out-of-range since）
  2) `decision = _decide(snapshot)`
  3) `_apply_patch(decision.patch)`（只在这里推进策略状态）
  4) 返回 `decision.actions`
- 同时输出完整 `processed_data`：`controller_state`、`intent_*`、rebalance/stoploss 关键字段。

## 6. 决策优先级（规则树）

从高到低：
1) `manual_kill_switch`（停止所有 LP）
2) `lp_failure_detected`（进入 failure block，需要人工）
3) 任意 swap executor active（全局串行，直接 WAIT）
4) stoploss（触发则 stop 全部 LP，并进入冷却/可选 liquidation）
5) rebalance stop（符合条件则对对应 LP 发 stop，并创建 plan）
6) 有 active LP 且无 rebalance plan（保持 ACTIVE/WAIT）
7) pending liquidation（提交 liquidation swap）
8) stoploss cooldown（等待）
9) rebalance reopen（延时到期后执行 swap/open）
10) entry（触发则 swap/open，否则 idle）

## 7. 为什么我们的 Rebalance 比官方 `lp_manager` 更复杂？

官方 `hummingbot/controllers/generic/lp_manager.py` 的 rebalance 逻辑极简，原因是它的目标也极简：
- 只支持 **单一 active executor**（`active_executor()` 取第一个）。
- rebalance 只做：`OUT_OF_RANGE + elapsed >= rebalance_seconds -> StopExecutorAction`；下一 tick 没 executor 就直接 `CreateExecutorAction`。
- 不做 inventory swap、不做预算锁、不做 stoploss、不做 cost filter、不处理 “action 只是建议、可能不被执行” 的情况。

而 `clmm_lp` 的 rebalance 复杂，主要来自四类“必须处理的真实约束”：
1) **多 executor**：每个 LP 都可能独立 out-of-range，需要按 `executor_id` 保存独立 plan（`RebalanceContext.plans`）。
2) **动作互斥**：rebalance reopen 前可能需要 inventory swap，swap 期间必须全局暂停 LP 开/关仓。
3) **频率/冷却/成本**：`hysteresis_pct`、`cooldown_seconds`、`max_rebalances_per_hour` 与 `cost_filter_*` 都会影响“是否 stop、何时 stop”。
4) **Controller/Strategy 契约**：Controller 输出的是 actions proposal，不应假设一定执行；因此需要：
   - STOP 阶段幂等重复输出 `StopExecutorAction`，直到观测到 LP 已关闭；
   - OPEN 阶段有超时回退（避免永远卡住）。

> 结论：复杂性主要是“能力范围扩大 + 契约更严格”带来的，目标是让行为在实盘环境更稳定可控，而不是为了拆函数而拆函数。

## 8. Rebalance 触发条件与阶段

### 8.1 触发条件（全部满足）
- 当前价落在 `[lower, upper]` 外（按策略语义）
- 偏离度 `deviation_pct >= hysteresis_pct`
- `now - out_of_range_since >= rebalance_seconds`
- `now - last_rebalance_ts >= cooldown_seconds`
- `_can_rebalance_now()` 通过（`max_rebalances_per_hour`）
- 若启用 cost filter：`CostFilter.allow_rebalance(...)` 通过；否则可能被 `should_force_rebalance()` 强制放行

### 8.2 Plan 阶段（`RebalanceStage`）
- `STOP_REQUESTED`：对该 `executor_id` 幂等发 stop，直到观测到 LP 不再 active。
- `WAIT_REOPEN`：等待 `reopen_delay_sec`，到期后进入 reopen（必要时 inventory swap）。
- `OPEN_REQUESTED`：已发 open action，等待新 executor 出现；超时则回退到 `WAIT_REOPEN`。

## 9. Cost Filter（独立模块）

位置：`bots/controllers/generic/clmm_lp_cost_filter.py`，职责是：
- 从 `LPView` 的 pending fees（转换成 quote）更新 `FeeEstimatorContext.fee_rate_ewma`。
- 依据固定窗口 `IN_RANGE_TIME_SEC` 与成本估算（fixed + swap 摩擦）判断是否允许 rebalance。
- 提供 `should_force_rebalance()`：长时间 out-of-range 允许绕过过滤，避免永远不 rebalance。

## 10. StopLoss 与 liquidation

- StopLoss 基于每个 LP 的 `BudgetAnchor`（quote 计价）：
  - equity = deployed_value（含 fees） + wallet_slice_value
  - 低于阈值则触发：stop 全部 LP，进入 `stop_loss_pause_sec` 冷却。
- 若 `stop_loss_liquidation_mode: quote`：
  - 触发 liquidation swap（base -> quote）。
  - `liquidation_target_base` 会在每次 liquidation swap 完成后递减，直到归零（或 wallet_base 为 0）。

## 11. `processed_data`（对外观测字段）

关键字段（以当前实现为准）：
- 价格/余额：`current_price`、`wallet_base`、`wallet_quote`
- view state：`controller_state`、`state_reason`
- intent：`intent_flow`、`intent_stage`、`intent_reason`
- stoploss：`pending_liquidation`、`stop_loss_active`、`stop_loss_until_ts`
- rebalance：`rebalance_pending`、`rebalance_plans`
- failure：`lp_failure_blocked`
- executors：`active_lp`、`active_swaps`

## 12. 配置要点（防止方向配错）

最常见误配是 “池子 token 顺序与策略顺序相反”：
- swap 按 `trading_pair`（策略 BASE-QUOTE）理解；
- LP 按 `pool_trading_pair`（池子 token0-token1）理解；
- 两者必须由 `pool_trading_pair` 明确桥接，不能靠猜。

示例：`bots/conf/controllers/clmm_lp.yml`（已给出）。

## 13. 验收（最小可验证标准）

- 编译检查：`python -m py_compile bots/controllers/generic/clmm_lp.py bots/controllers/generic/clmm_lp_components.py bots/controllers/generic/clmm_lp_cost_filter.py`
- 运行观察：启动 bot 后 `processed_data` 中不再出现 `router_price` 字段；rebalance/stoploss/entry 的 intent 与 actions 能对应到实际 executors 状态变化。
