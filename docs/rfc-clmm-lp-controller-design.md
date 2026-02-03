# RFC: CLMM LP Controller — 边界定义、FSM 与恢复原则

## 1. 背景与问题
CLMM LP 策略涉及链上头寸与余额快照等多源数据。过去在 RPC / Gateway 不稳定时，出现以下问题：

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

1) **余额快照为权威**
   - BalanceManager 负责刷新余额并标记 `balance_fresh`
   - 当余额 stale 时请求刷新，不阻塞 FSM

2) **Stoploss 仅在 price_valid 时触发**
   - 价格不可用时跳过止损，避免误触发

3) **价格权威顺序：RateOracle(quote-swap) 单口径**
   - CLMM 决策统一使用 quote-swap 报价，不再混用池内价格

4) **LP Executor 数据不可用时必须显式标记**
   - 观察层显示 UNKNOWN，而不是隐式继续使用旧值

## 3. 机制概览（数据链路与状态机）
### 3.1 关键数据源
- **Wallet snapshot**：connector `update_balances()` 的可用余额
- **Ledger**：基于 balance events 的增量账本
- **Quote price**：RateOracle/quote-swap 报价（唯一决策口径）
- **LP position info**：`connector.get_position_info()`

### 3.2 数据流
1. 每 tick 调用 `BalanceManager.schedule_refresh`
2. 生成 Snapshot（包含 balance_fresh、current_price、lp views、swaps）
3. FSM 根据 Snapshot 产生 Decision

## 4. 权威数据源边界定义（余额 / 价格 / LP 信息）
### 4.1 余额：Snapshot 为权威
- 余额来源统一为 BalanceManager 的快照
- 通过强制刷新解决短期不一致（LP 开/关仓、swap 完成）

### 4.2 价格：Quote-swap 单口径
- 决策统一使用 RateOracle/quote-swap 报价
- pool price 不参与决策，避免多源口径冲突

### 4.3 LP 信息：Executor vs UI
- FSM 只使用 executor 信息（LPView），不使用 last snapshot
- UI 可使用 last snapshot 作为观察兜底，但必须标记为 REBALANCE/UNKNOWN
- 当 executor 数据不可用时，必须显式暴露 `UNKNOWN` 状态

## 5. 关键状态与转移（FSM 说明）
状态集合：
- IDLE
- ENTRY_OPEN → ACTIVE
- REBALANCE_STOP → REBALANCE_OPEN
- TAKE_PROFIT_STOP
- STOPLOSS_STOP → EXIT_SWAP
- COOLDOWN

关键规则：
- 仅允许单一活动 LP（并发 guard）
- Entry 由 `target_price` 与 `trigger_above` 决定
- Rebalance 由 out_of_range + 时间阈值 + hysteresis + cooldown 决定
- Stoploss 优先于 rebalance，但需满足 price_valid
- 若 `exit_full_liquidation=true`，止盈/止损后进入 `EXIT_SWAP` 清算

### 5.1 状态转移表（核心路径）

| 当前状态 | 触发条件 | 目标状态 | 关键动作/说明 |
| --- | --- | --- | --- |
| IDLE | 触发入场条件满足 | ENTRY_OPEN | 直接开仓 |
| ENTRY_OPEN | LP 成功创建 | ACTIVE | 设置 anchor |
| ACTIVE | out_of_range & 达到 rebalance 门槛 | REBALANCE_STOP | 关闭 LP |
| REBALANCE_STOP | LP 已关闭 | REBALANCE_OPEN | 重新开仓 |
| REBALANCE_OPEN | LP 成功创建 | ACTIVE | 更新 anchor |
| 任意 | Stoploss 触发 | STOPLOSS_STOP | 强制关闭 LP |
| STOPLOSS_STOP | LP 已关闭 | EXIT_SWAP/COOLDOWN | 可选清算 base→quote |
| TAKE_PROFIT_STOP | LP 已关闭 | EXIT_SWAP/IDLE | 可选清算 |
| EXIT_SWAP | swap 完成 | COOLDOWN/IDLE | 依据退出原因 |
| COOLDOWN | 冷却结束 | IDLE | 重新评估入场 |

### 5.2 典型流程序列（简化）

**正常入场：**  
IDLE → ENTRY_OPEN → ACTIVE

**出区间再平衡：**  
ACTIVE → REBALANCE_STOP → REBALANCE_OPEN → ACTIVE

**止损流程：**  
ACTIVE/IDLE → STOPLOSS_STOP → EXIT_SWAP/COOLDOWN → IDLE

**异常恢复（余额漂移）：**  
异常阶段 → 触发强制刷新 → IDLE → 正常入场

## 6. 变更与差异（相对旧行为）
### 6.1 余额权威边界
- 旧：多源余额不一致时无法自动恢复
- 新：snapshot 为权威，缺失时通过刷新恢复

### 6.2 价格优先级
- 旧：多源价格混用，存在口径冲突
- 新：RateOracle/quote-swap 单口径

### 6.3 Stoploss 前置条件
- 旧：balance_fresh + price_valid gating
- 新：仅 price_valid gating（余额刷新由 BalanceManager 异步触发，不阻塞止损）

### 6.4 观察窗口消失
- 旧：nav 为空 → custom_info 清空 → UI 没有 LP range
- 新：custom_info 不再清空，保留 last snapshot + UNKNOWN 状态

## 7. 风险与权衡
- **Ledger reset 风险**：若存在隐藏未结算事件，强制 reset 可能丢失事件影响
  - 通过“无 LP/Swap”条件限制该风险
- **Quote-swap 单口径**：不再依赖 pool-info，避免多源口径冲突
- **Stoploss gating**：在 balance 不 fresh 时，止损会被抑制
  - 这是安全优先的取舍

## 8. 附录：日志示例与典型故障时间线
### 8.1 典型症状（节选）
- `update_balances failed ... TimeoutError`
- `Gateway error: Pool not found ...`（RPC 抖动导致）
- `State: IDLE | Wallet: 361 / -1314`（余额漂移）

### 8.2 典型故障链路
1. RPC 抖动 → balance/position-info/pool-info 频繁失败
2. Ledger 与 snapshot 漂移加大，无法 reconcile
3. FSM 被连续异常阻断 → 永久 IDLE
4. UI custom_info 被清空 → LP 观察窗口消失

### 8.3 恢复原则验证
- 当余额 stale 时触发刷新，恢复后 FSM 继续推进
