# RFC: CLMM LP Controller — 边界定义、FSM 与恢复原则

## 1. 背景与问题
CLMM LP 策略涉及链上头寸、余额快照与事件账本（ledger）多源数据。过去在 RPC / Gateway 不稳定时，出现以下问题：

- LP 头寸已关闭或已出区间，但 UI 观察窗口消失，无法判断当前状态
- 余额恢复后策略仍长期停留在 IDLE，无法重新入场
- Wallet 余额出现负数或与真实余额严重偏离
- pool-info / position-info 偶发 NotFound，误判为池子不存在

这些问题并非单点 bug，而是**权威数据源边界未定义**导致的链路性失效：当多源数据彼此不一致时，系统没有明确规则去恢复一致性，最终触发“恢复后仍卡死”。

## 2. 目标与恢复原则（权威边界）
本 RFC 目标：

- 明确 CLMM 策略的**权威数据源边界**
- 定义恢复原则，使系统在异常后可自动回到稳定状态
- 在不增加 FSM 复杂度的前提下保证可恢复性

恢复原则（核心规则）：

1) **无 LP/Swap 时，snapshot 为权威**
   - 当无活跃 LP/Swap 且余额快照 fresh 时，ledger 必须被重置到 snapshot
   - 这是恢复死锁的根原则

2) **Stoploss 只有在 balance_fresh + price_valid 时触发**
   - 避免余额不可靠或价格不可用时误触发止损

3) **价格权威顺序：Pool Price > Router Price**
   - CLMM 的决策应优先使用池内价格，router 价格仅为兜底

4) **LP Executor 数据不可用时必须显式标记**
   - 观察层显示 UNKNOWN，而不是隐式继续使用旧值

## 3. 机制概览（数据链路与状态机）
### 3.1 关键数据源
- **Wallet snapshot**：connector `update_balances()` 的可用余额
- **Ledger**：基于 balance events 的增量账本
- **Pool price**：`connector.get_pool_info(pool)`
- **Router price**：market_data_provider 的路由报价
- **LP position info**：`connector.get_position_info()`

### 3.2 数据流
1. 每 tick 调用 `BalanceManager.schedule_refresh` 与 `PoolPriceManager.schedule_refresh`
2. 生成 Snapshot（包含 balance_fresh、current_price、lp views、swaps）
3. Ledger 应用事件并生成 LedgerStatus
4. FSM 根据 Snapshot 与 LedgerStatus 产生 Decision

## 4. 权威数据源边界定义（余额 / 价格 / LP 信息）
### 4.1 余额：Ledger vs Snapshot
- **有活跃 LP/Swap 时**：ledger 为主（因为可能存在未结算事件）
- **无活跃 LP/Swap 且 balance_fresh 时**：snapshot 为主，ledger 必须强制重置
- 该规则保证异常恢复后不会长期卡死

### 4.2 价格：Pool vs Router
- pool price 反映 CLMM 当前 tick 状态，为主
- router price 仅作为 fallback（pool-info 不可用时）
- 该规则避免 price 漂移导致的策略误判

### 4.3 LP 信息：Executor vs UI
- FSM 只使用 executor 信息（LPView），不使用 last snapshot
- UI 可使用 last snapshot 作为观察兜底，但必须标记为 REBALANCE/UNKNOWN
- 当 executor 数据不可用时，必须显式暴露 `UNKNOWN` 状态

## 5. 关键状态与转移（FSM 说明）
状态集合：
- IDLE
- ENTRY_SWAP → ENTRY_OPEN → ACTIVE
- REBALANCE_STOP → REBALANCE_SWAP → REBALANCE_OPEN
- STOPLOSS_STOP → STOPLOSS_SWAP
- COOLDOWN

关键规则：
- 仅允许单一活动 LP（并发 guard）
- Entry 由 `target_price` 与 `trigger_above` 决定
- Rebalance 由 out_of_range + 时间阈值 + cost filter 决定
- Stoploss 优先于 rebalance，但需满足 balance_fresh + price_valid

### 5.1 状态转移表（核心路径）

| 当前状态 | 触发条件 | 目标状态 | 关键动作/说明 |
| --- | --- | --- | --- |
| IDLE | 触发入场条件满足 | ENTRY_OPEN | 直接开仓或进入 swap 逻辑 |
| IDLE | 需要调整库存 | ENTRY_SWAP | 构建 inventory swap |
| ENTRY_SWAP | swap 完成 | ENTRY_OPEN | 进入开仓 |
| ENTRY_OPEN | LP 成功创建 | ACTIVE | 设置 anchor |
| ACTIVE | out_of_range & 达到 rebalance 门槛 | REBALANCE_STOP | 关闭 LP |
| REBALANCE_STOP | LP 已关闭 | REBALANCE_SWAP | 进入重平衡 swap |
| REBALANCE_SWAP | swap 完成 | REBALANCE_OPEN | 重新开仓 |
| REBALANCE_OPEN | LP 成功创建 | ACTIVE | 更新 anchor |
| 任意 | Stoploss 触发 | STOPLOSS_STOP | 强制关闭 LP |
| STOPLOSS_STOP | LP 已关闭 | STOPLOSS_SWAP | 全额 base→quote |
| STOPLOSS_SWAP | swap 完成 | COOLDOWN | 冷却期 |
| COOLDOWN | 冷却结束 | IDLE | 重新评估入场 |

### 5.2 典型流程序列（简化）

**正常入场：**  
IDLE → ENTRY_OPEN（或 ENTRY_SWAP） → ACTIVE

**出区间再平衡：**  
ACTIVE → REBALANCE_STOP → REBALANCE_SWAP → REBALANCE_OPEN → ACTIVE

**止损流程：**  
ACTIVE/IDLE → STOPLOSS_STOP → STOPLOSS_SWAP → COOLDOWN → IDLE

**异常恢复（ledger 漂移）：**  
异常阶段 → 无 LP/Swap + balance_fresh → ledger reset → IDLE → 正常入场

## 6. 变更与差异（相对旧行为）
### 6.1 余额权威边界
- 旧：ledger 与 snapshot 不一致时无法自动恢复
- 新：无 LP/Swap 时 snapshot 为权威，ledger 强制 reset

### 6.2 价格优先级
- 旧：router price 优先，pool price 作为 fallback
- 新：pool price 优先，router price 作为 fallback

### 6.3 Stoploss 前置条件
- 旧：price 有就触发 stoploss，不考虑余额是否可靠
- 新：必须 balance_fresh + price_valid 才能触发

### 6.4 观察窗口消失
- 旧：nav 为空 → custom_info 清空 → UI 没有 LP range
- 新：custom_info 不再清空，保留 last snapshot + UNKNOWN 状态

## 7. 风险与权衡
- **Ledger reset 风险**：若存在隐藏未结算事件，强制 reset 可能丢失事件影响
  - 通过“无 LP/Swap”条件限制该风险
- **Pool price 优先**：若 pool-info 不稳定，仍需 router 兜底
- **Stoploss gating**：在 balance 不 fresh 时，止损会被抑制
  - 这是安全优先的取舍

## 8. 附录：日志示例与典型故障时间线
### 8.1 典型症状（节选）
- `update_balances failed ... TimeoutError`
- `pool_price_update_failed ... TimeoutError`
- `Gateway error: Pool not found ...`（RPC 抖动导致）
- `State: IDLE | Wallet: 361 / -1314`（ledger 漂移）

### 8.2 典型故障链路
1. RPC 抖动 → balance/position-info/pool-info 频繁失败
2. Ledger 与 snapshot 漂移加大，无法 reconcile
3. FSM 被 ledger_guard 阻断 → 永久 IDLE
4. UI custom_info 被清空 → LP 观察窗口消失

### 8.3 恢复原则验证
- 当 LP/Swap 全为空 + balance_fresh = True 时，ledger 强制 reset → FSM 继续推进
