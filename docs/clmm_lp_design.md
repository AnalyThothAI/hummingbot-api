# CLMM LP 控制器设计说明

本文参考 `hummingbot/docs/design/lp_controller_executor_design.md` 的设计范式，详细说明本仓库的 CLMM LP 控制器
（`clmm_lp`）实现：职责边界、状态机、资金/数量链路、核心决策逻辑、失败处理与未来演进。

## 1. 范围与参考

- Controller 实现：`bots/controllers/generic/clmm_lp.py`
- Controller 配置：`bots/conf/controllers/clmm_lp.yml`
- Loader 脚本：`bots/scripts/v2_with_controllers.py`
- LP Executor：`hummingbot/hummingbot/strategy_v2/executors/lp_position_executor/`
- Swap Executor：`hummingbot/hummingbot/strategy_v2/executors/gateway_swap_executor/`
- 预算模块：`hummingbot/hummingbot/strategy_v2/budget/`

本文描述当前实现（包含固定预算池与库存管理）与官方设计的一致性与扩展点。

## 2. 设计原则（与官方文档一致）

- **职责分离**：Controller 只做决策、Executor 只做执行。
- **显式状态机**：控制器维护单一状态机，Executor 自有状态机。
- **单一时间源**：使用 `market_data_provider.time()` 作为时钟。
- **安全优先**：预算保护、链上动作串行化、错误可恢复。
- **可组合**：控制器输出 `ExecutorAction`，不直接操作连接器。

## 3. 架构概览

```
Controller (clmm_lp)
  ├─ LPPositionExecutor (开/关仓、状态上报)
  ├─ GatewaySwapExecutor (库存调整、止损清仓)
  ├─ BudgetCoordinator (钱包余额锁)
  └─ FixedBudgetPool (可选：固定预算池)
```

### 3.1 Controller 角色
- 读取市场价格、executor 状态与预算快照。
- 决定：何时开仓、何时平仓、何时补仓/换仓、何时止损。
- 只输出 `CreateExecutorAction` / `StopExecutorAction`。

### 3.2 LPPositionExecutor 角色
- 维护 LP 头寸生命周期状态机（OPENING/IN_RANGE/OUT_OF_RANGE/CLOSING/...）。
- 通过 `custom_info` 上报状态和数量：`state`、`current_price`、`lower_price`、`upper_price`、
  `base_amount`、`quote_amount`、`base_fee`、`quote_fee`、`out_of_range_since`。

### 3.3 GatewaySwapExecutor 角色
- 执行 swap（库存调整或止损清仓）。
- 通过 `custom_info` 回传实际 `amount_in/amount_out`，用于预算结算。

## 4. 配置规范（必须遵守）

- `controller_name` 必须与模块文件名一致：`clmm_lp`。
- `controller_type` 必须对应目录：`generic`。
- `id` 为 Controller 实例唯一标识，**推荐与配置文件名一致**。

推荐示例：
- 配置文件名：`clmm_lp.yml`
- `id: clmm_lp`
- `controller_name: clmm_lp`

## 5. Controller 状态机（显式）

> 控制器状态机是高层决策状态，不等同于 LP Executor 的内部状态。

### 5.1 状态列表
- **IDLE**：无 LP 仓位，无待执行动作。
- **ACTIVE**：LP Executor 运行中。
- **REBALANCE_WAIT_CLOSE**：已发出 stop 等待重开（包含 reopen delay）。
- **INVENTORY_SWAP**：触发库存调整 swap。
- **READY_TO_OPEN**：准备开仓（预算/校验完成）。
- **WAIT_SWAP**：swap executor 在运行，暂停其他动作。
- **STOPLOSS_PAUSE**：止损触发后的冷却期。
- **MANUAL_STOP**：人工止损（manual_kill_switch）。

### 5.2 状态迁移逻辑（要点）
- MANUAL_STOP 优先级最高，直接 stop LP executor。
- swap executor 运行时，控制器进入 WAIT_SWAP，避免并发动作。
- LP executor 运行时：
  - 优先评估 stop loss；
  - 再评估 rebalance。
- stop loss 或 rebalance 触发后，先 stop LP，再进入重开或清仓流程。
- stop loss 冷却期内禁止重新开仓。

### 5.3 Tick 决策顺序（精简伪代码）

```
if manual_kill_switch:
  stop LP; state=MANUAL_STOP; return

if swap_executor active:
  state=WAIT_SWAP; return

if pending_liquidation:
  create swap; state=WAIT_SWAP; return

if LP executor active:
  if stop_loss_triggered:
    stop LP; state=STOPLOSS_PAUSE; return
  if rebalance_triggered:
    stop LP; state=REBALANCE_WAIT_CLOSE; return
  state=ACTIVE; return

if now < stop_loss_until_ts:
  state=STOPLOSS_PAUSE; return

if pending_rebalance:
  if now < reopen_after_ts: state=REBALANCE_WAIT_CLOSE; return
  if auto_swap and not swap_attempted and not single_sided:
    create swap; state=INVENTORY_SWAP; return
  open LP (single or both); state=ACTIVE; return

if not entry_triggered:
  state=IDLE; return

if auto_swap and not swap_attempted:
  create swap; state=INVENTORY_SWAP; return

open LP; state=ACTIVE
```

## 6. 数量/资金链路（预算链路）

### 6.1 预算模式
- **WALLET**：直接使用钱包余额；BudgetCoordinator 负责锁定。
- **FIXED**：使用 FixedBudgetPool（固定预算池），同时仍使用 BudgetCoordinator 做钱包检查和 gas 预留。

### 6.2 开仓数量计算
- 若启用固定预算：
  - 使用 `target_base_value_pct`（可为 0-1 或 0-100）计算目标 base 价值占比。
  - `_calculate_target_allocation_amounts` 根据当前价格与可用预算分配 base/quote。
- 若未启用固定预算：
  - 直接使用 `base_amount` / `quote_amount`。

### 6.3 预算锁与结算
- 开仓前：
  - FixedBudgetPool 先 `reserve()` 锁定预算；
  - BudgetCoordinator 再 `reserve()` 校验钱包可用余额与 gas 预留。
- 平仓后：
  - 使用 LP executor 的 `custom_info` 实际数量 `base_amount/quote_amount + fee` 结算回池。

### 6.4 交换（swap）预算
- 交换前：
  - 在 FixedBudgetPool 预留输入资产（token_in）。
- 交换后：
  - **使用实际成交量** `amount_out` 回填预算。
  - 失败则释放预留。

## 7. 再平衡逻辑

触发条件全部满足：
- LP executor 状态为 `OUT_OF_RANGE`；
- 偏离范围超过 `hysteresis_pct`；
- out_of_range 时长超过 `rebalance_seconds`；
- 满足冷却时间 `cooldown_seconds`；
- 小时内重平衡次数未超过 `max_rebalances_per_hour`。

触发后流程：
1) Stop LP executor。
2) 等待 `reopen_delay_sec`。
3) 若开启 `auto_swap` 且未失败，执行库存调整 swap。
4) 开新 LP。若 swap 失败则单边开仓（保留原一侧余额）。

## 8. 入场逻辑

- `target_price <= 0` 时不限制价格。
- `trigger_above == true`：价格 >= target_price 才入场。
- `trigger_above == false`：价格 <= target_price 才入场。
- stop loss 后 `reenter_enabled` 为 false 时禁止再入场。

## 9. 止损逻辑

- 开仓时记录锚定价值：`anchor = base_amount * price + quote_amount`。
- 当 `executor.net_pnl_quote <= -anchor * stop_loss_pnl_pct` 触发止损。
- 触发后进入 `STOPLOSS_PAUSE`，持续 `stop_loss_pause_sec`。
- 若 `stop_loss_liquidation_mode == quote`：
  - 触发清仓 swap（base -> quote）。

> 注意：止损基于 executor 的 `net_pnl_quote`，不是全账户 PnL。

## 10. 连接器/网关链路

- LP 执行通过 `connector_name`（如 `uniswap/clmm`）。
- Swap 通过 `router_connector`（如 `pancakeswap/router`）。
- Controller 的 `update_markets` 必须注册两者。

## 11. 关键配置项说明（精选）

- `position_width_pct`：价格区间宽度百分比。
- `hysteresis_pct`：出界后再平衡的偏离阈值。
- `rebalance_seconds` / `cooldown_seconds`：出界持续时长 / 冷却时间。
- `reopen_delay_sec`：stop 后延时重开。
- `auto_swap_enabled` / `target_base_value_pct`：库存管理开关与目标比例。
- `swap_min_quote_value`：库存调整的最小价值阈值。
- `swap_safety_buffer_pct`：swap 输入安全缓冲。
- `stop_loss_pnl_pct` / `stop_loss_pause_sec`：止损阈值与冷却时间。
- `stop_loss_liquidation_mode`：止损后是否换成 quote。
- `budget_mode` / `fixed_budget_base` / `fixed_budget_quote`：预算模式与金额。
- `budget_key`：预算隔离键（默认 `id`）。
- `native_token_symbol` / `min_native_balance`：gas 预留。

## 12. 风险控制与异常处理

- swap 失败：
  - 不阻塞系统；可以单边开仓。
- LP 失败：
  - FixedBudgetPool 预留会被保留（日志提示，需人工处理）。
- 避免并发链上动作：
  - swap executor 运行时禁止 LP 开仓；
  - BudgetCoordinator 内部 `action_lock` 可用于串行化（Executor 自身使用）。

## 13. 不变式（Invariant）

- 同一 Controller 只允许一个 LP executor 处于 ACTIVE。
- swap executor 运行时 Controller 不触发其他执行动作。
- 开仓必须通过预算 reservation。

## 14. 已知限制

- FixedBudgetPool 仅内存实现，重启后重置。
- stop loss 依赖 executor 报告的 PnL；不同 DEX 实现可能存在误差。
- 无多层（core/edge）与多池组合编排。

## 15. 未来计划

- 引入 core/edge 多层 LP 控制器并共享预算。
- 将 FixedBudgetPool 持久化（重启恢复）。
- 引入更细粒度的 swap 重试和失败恢复策略。
- 增加 Controller 状态机与预算的单元测试。
- Dashboard 增强：展示 Controller 状态与预算池快照。
