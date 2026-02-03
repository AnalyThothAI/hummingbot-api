# CLMM LP Controller 设计（`clmm_lp_uniswap` / `clmm_lp_meteora`）

本文描述当前 CLMM LP 控制器的设计与边界，按 Hummingbot v2 Controller 实践组织：`update_processed_data()` 负责观测与视图，`determine_executor_actions()` 负责决策与状态推进。

## 1. 目标与原则（先定“改什么，不改什么”）

**不改的部分（保持策略能力与框架契约）**
- 每个实例仍然是 **单一 Controller orchestrator**（不拆多个 controller 并行跑，避免互相打架）。
- Controller 只输出 `CreateExecutorAction` / `StopExecutorAction`，不直接下单/改余额。
- 执行仍由 Executors 完成：`LPPositionExecutor`（开/关仓）与 `GatewaySwapExecutor`（swap）。
- 支持 **同一 controller 多个 active executors**，按 `executor_id` 维护独立上下文。

**要改的部分（可读、可维护、契约清晰）**
- 把 token 顺序/反转问题集中到 `TokenOrderMapper`（适配层）。
- 移除成本过滤与自动 swap/normalization 路径，保留“退出时可选 liquidation swap”。
- Controller 主流程固定为：`snapshot -> fsm.step -> actions`（实现在 `clmm_lp_base.py`）。
- 日志只保留关键异常/告警，不做大量节流/Debug 采样逻辑（避免性能与维护负担）。

## 2. 文件入口

- Controller（共享逻辑）：`bots/controllers/generic/clmm_lp_base.py`
- Controller（Uniswap）：`bots/controllers/generic/clmm_lp_uniswap.py`
- Controller（Meteora）：`bots/controllers/generic/clmm_lp_meteora.py`
- Types / Adapters / Context：`bots/controllers/generic/clmm_lp_domain/components.py`
- 配置示例：`bots/conf/controllers/clmm_lp_uniswap.yml` / `bots/conf/controllers/clmm_lp_meteora.yml`
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
位置：`bots/controllers/generic/clmm_lp_domain/components.py`。
- `strategy_amounts_to_lp()` / `strategy_bounds_to_lp()`：开仓时把金额与区间映射到 pool 顺序。
- `lp_amounts_to_strategy()` / `lp_bounds_to_strategy()`：解析 executor 上报时映射回策略顺序。

### 4.2 Portfolio & Budget（观测层）
Controller 维护的钱包与派生数据：
- `wallet_base/wallet_quote`：来自 connector balances（按策略 `trading_pair` 的 token）。
- `BudgetAnchor`：用于 stoploss 的“预算切片锚定值”（按 quote 计价），存在 `ControllerContext.anchor_value_quote`。

### 4.3 Flow（决策层）
三个 flow（都在 `clmm_lp_base.py` 内，以 `Decision/Intent` 表达）：
- Entry：直接按钱包余额开 LP（支持单边或双边）。
- Rebalance：出界 -> stop -> reopen（不做 inventory swap）。
- StopLoss/TakeProfit：触发后 stop 全部 LP；根据 `exit_full_liquidation` 决定是否执行 liquidation swap（base->quote）。

## 5. Controller 边界（完全对齐 v2 实践）

### 5.1 `update_processed_data()`：观测与视图
- 允许做 IO：刷新 balances。
- 允许做“观测驱动”的 ctx 更新：检测 LP 开/关仓并触发强制余额刷新。
- 构建 `Snapshot` 并缓存到 `_latest_snapshot`，同时输出基础 `processed_data`（价格/余额/active executors 列表）。

### 5.2 `determine_executor_actions()`：决策与状态推进
- 读取 `_latest_snapshot`（或当次构建），然后：
  1) `decision = fsm.step(snapshot, ctx)`
  2) 返回 `decision.actions`
- 同时输出完整 `processed_data`：`state`、`risk`、`rebalance`、`lp/swaps` 等关键字段。

## 6. 决策优先级（规则树）

从高到低：
1) `manual_kill_switch`（停止所有 LP）
2) `lp_failure_detected`（进入 failure block，需要人工）
3) 任意 swap executor active（全局串行，直接 WAIT）
4) stoploss（触发则 stop 全部 LP，并进入冷却/可选 liquidation）
5) take-profit（触发则 stop LP）
6) rebalance stop（符合条件则对对应 LP 发 stop）
7) 有 active LP 且无 rebalance plan（保持 ACTIVE/WAIT）
8) exit liquidation（执行 base->quote swap）
9) stoploss cooldown（等待）
10) rebalance reopen（延时到期后执行 open）
11) entry（触发则 open，否则 idle）

## 7. 为什么我们的 Rebalance 比官方 `lp_manager` 更复杂？

官方 `hummingbot/controllers/generic/lp_manager.py` 的 rebalance 逻辑极简，原因是它的目标也极简：
- 只支持 **单一 active executor**（`active_executor()` 取第一个）。
- rebalance 只做：`OUT_OF_RANGE + elapsed >= rebalance_seconds -> StopExecutorAction`；下一 tick 没 executor 就直接 `CreateExecutorAction`。
- 不做 inventory swap、不做预算锁、不做 stoploss、不做 cost filter、不处理 “action 只是建议、可能不被执行” 的情况。

而 `clmm_lp_base` 的 rebalance 复杂，主要来自四类“必须处理的真实约束”：
1) **并发保护**：同一 controller 只允许一个 LP 与一个 swap 活跃，避免互相干扰。
2) **动作互斥**：swap 与 LP 变更串行化（exit swap 期间暂停 LP 开/关仓）。
3) **频率/冷却**：`hysteresis_pct`、`cooldown_seconds`、`max_rebalances_per_hour` 会影响“是否 stop、何时 stop”。
4) **Controller/Strategy 契约**：Controller 输出的是 actions proposal，不应假设一定执行；因此需要：
   - STOP 阶段幂等重复输出 `StopExecutorAction`，直到观测到 LP 已关闭；
   - OPEN 阶段有超时回退（避免永远卡住）。

> 结论：复杂性主要是“能力范围扩大 + 契约更严格”带来的，目标是让行为在实盘环境更稳定可控，而不是为了拆函数而拆函数。

## 8. Rebalance 触发条件与阶段

### 8.1 触发条件（全部满足）
- `rebalance_enabled` 为 `false` 时 **不会触发**（默认关闭，需显式开启）
- `rebalance_seconds <= 0` 视为禁用 rebalance
- 当前价落在 `[lower, upper]` 外（按策略语义）
- 偏离度 `deviation_pct >= hysteresis_pct`
- `now - out_of_range_since >= rebalance_seconds`
- `now - last_rebalance_ts >= cooldown_seconds`
- `_can_rebalance_now()` 通过（`max_rebalances_per_hour`）

### 8.2 阶段（显式 FSM）
- `REBALANCE_STOP`：对 LP 幂等发 stop，直到观测到 LP 已关闭。
- `REBALANCE_OPEN`：发送 open action，若超时则回退到 `IDLE`。

## 9. Cost Filter（已移除）

已移除成本过滤与相关上下文，rebalance 仅基于价格越界 + 冷却/频率条件判断。

## 10. StopLoss 与 liquidation

- StopLoss 基于 anchor equity（quote 计价）：
  - equity = LP deployed_value（含 fees，仅 LP 资产）
  - 低于阈值则触发：stop LP，进入 `stop_loss_pause_sec` 冷却。
- LP stop 完成后，若 `exit_full_liquidation=true`，触发一次全额 base -> quote 清算（按钱包快照）。
- Take-profit（`take_profit_pnl_pct`）触发后进入 `TAKE_PROFIT_STOP`：
  - 发送 `StopExecutorAction` 关闭 LP
  - LP 关闭完成后回到 `IDLE`，或在 `exit_full_liquidation=true` 时进入 `EXIT_SWAP`
  - 不做 inventory swap（只平仓 LP）
  - 若 `reenter_enabled=false`，止盈/止损后不再自动入场（需手动重启/更新配置）

## 11. `processed_data`（对外观测字段）

关键字段（以当前实现为准）：
- state：`state.value` / `state.since` / `state.reason`
- heartbeat：`heartbeat.last_tick_ts` / `heartbeat.tick_age_sec`
- price：`price.value` / `price.source` / `price.timestamp`
- wallet：`wallet.base` / `wallet.quote` / `wallet.value_quote`
- lp：`lp.active_count` / `lp.value_quote` / `lp.positions`
- swaps：`swaps.active_count` / `swaps.active`
- risk：`risk.anchor_quote` / `risk.equity_quote` / `risk.exit_full_liquidation`
- rebalance：`rebalance.out_of_range_since` / `rebalance.count_1h` / `rebalance.signal_reason`
- diagnostics：`diagnostics.balance_fresh` / `diagnostics.domain_ready`

## 12. 配置要点（防止方向配错）

最常见误配是 “池子 token 顺序与策略顺序相反”：
- swap 按 `trading_pair`（策略 BASE-QUOTE）理解；
- LP 按 `pool_trading_pair`（池子 token0-token1）理解；
- 两者必须由 `pool_trading_pair` 明确桥接，不能靠猜。

补充配置：
- `rebalance_enabled` 默认 `false`，需要时显式设为 `true`
- `rebalance_seconds <= 0` 等价于禁用 rebalance
- `reenter_enabled` 默认 `false`，为 `false` 时止盈/止损后不再自动入场
- `exit_full_liquidation` 控制止盈/止损后是否执行 base->quote 清算
- `exit_swap_slippage_pct` 控制清算 swap 的滑点上限
- `max_exit_swap_attempts` 控制清算 swap 的最大尝试次数

示例：`bots/conf/controllers/clmm_lp_uniswap.yml` / `bots/conf/controllers/clmm_lp_meteora.yml`（已给出）。

## 13. 验收（最小可验证标准）

- 编译检查：`python -m py_compile bots/controllers/generic/clmm_lp_base.py bots/controllers/generic/clmm_lp_uniswap.py bots/controllers/generic/clmm_lp_meteora.py bots/controllers/generic/clmm_lp_domain/components.py`
- 运行观察：启动 bot 后 `processed_data` 中不再出现 `router_price` 字段；rebalance/stoploss/entry 的 intent 与 actions 能对应到实际 executors 状态变化。
