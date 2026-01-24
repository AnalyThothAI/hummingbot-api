# LP Hedge 控制器设计说明

本文参考 `docs/clmm_lp_design.md` 的结构与风格，给出本仓库 LP 对冲控制器（`lp_hedge`）的设计说明：
职责边界、状态机、关键计算、执行策略、风控边界与参数建议。

> 重要前提：**LP 风险是非线性的（short gamma）**，对冲只能做到**一阶（delta）近似**，无法长期、低成本、零风险地完美对冲。
> 生产可行形态是：**LP 赚手续费/激励 + 永续合约做动态 delta hedge**，并让手续费覆盖 LVR/IL + 对冲成本 + funding/basis。

## 1. 范围与参考

- Controller 实现：`bots/controllers/generic/lp_hedge.py`
- Controller 配置：`bots/conf/controllers/lp_hedge.yml`
- LP Controller（基础）：`bots/controllers/generic/clmm_lp_base.py`
- LP Executor：`hummingbot/hummingbot/strategy_v2/executors/lp_position_executor/`
- 对冲 Executor：
  - `hummingbot/hummingbot/strategy_v2/executors/order_executor/`
  - `hummingbot/hummingbot/strategy_v2/executors/twap_executor/`
- Loader 脚本：`bots/scripts/v2_with_controllers.py`

## 2. 设计原则（与 clmm_lp 一致）

- **职责分离**：Controller 只做决策，Executor 只做执行。
- **显式状态机**：HedgeState 与 LP 状态门禁解耦。
- **单一时间源**：统一使用 `market_data_provider.time()`。
- **不完美对冲**：目标是“可控的部分对冲”，非零残差可接受。
- **风控优先**：资金/频率/互斥优先于收益。

## 3. 架构概览

```
Controller (lp_hedge)
  ├─ LPPositionExecutor (开/关仓、范围状态)
  ├─ OrderExecutor (小额对冲)
  ├─ TWAPExecutor (大额对冲)
  └─ MarketDataProvider (价格/余额/资金费率)
```

### 3.1 角色划分
- **LPPositionExecutor**：负责 LP 头寸生命周期与状态上报（IN_RANGE/OUT_OF_RANGE/OPENING/CLOSING）。
- **Hedge Executor**：永续合约下单（小额市价或大额 TWAP）。
- **Controller**：
  - 读取 LP `custom_info` 与对冲仓位/余额；
  - 计算净 delta 与目标对冲仓位；
  - 在 band/hysteresis/interval/cooldown 下触发对冲动作。

## 4. 关键概念与计算

### 4.1 净 delta 定义（部分对冲）

- **LP delta**：`base_amount (+ base_fee 可选)`
- **钱包现货**：`wallet_base`（可选）
- **合约仓位**：按净多空合并为 `position_base`

**净敞口**：
```
B_net = B_lp (+ B_fee) + B_wallet - B_perp
```

**目标对冲**：
```
B_target = - hedge_ratio * B_net
```
其中 `0 < hedge_ratio < 1` 为“部分对冲”，可降低频繁调仓与 funding 成本。

### 4.2 Band / Hysteresis / Interval / Cooldown

- **band**：当 `|delta_quote| < band` 不调仓。
- **hysteresis**：退出阈值更小，防抖。
- **interval**：时间触发兜底（例如每 5 分钟强制检查）。
- **cooldown**：刚对冲后短期不再调整。

配置映射：
- `hedge_delta_band_quote`
- `hedge_delta_band_pct`（按 LP 规模动态放大带宽）
- `hedge_delta_hysteresis_quote`
- `hedge_rebalance_interval_seconds`
- `hedge_cooldown_seconds`

### 4.3 对冲触发事件（类 XEMM 思路）

XEMM 的“maker 成交 → taker 对冲”在 LP 中表现为：
- LP 头寸 `base_amount` 漂移超过 band；
- LP out-of-range 持续超过阈值；
- 时间触发（interval）。

Controller 以“敞口变化事件”驱动下单，而非每 tick 追价。

## 5. 控制器状态机（HedgeState）

### 5.1 状态列表
- **DISABLED**：对冲关闭。
- **READY**：可评估对冲条件。
- **ADJUSTING**：对冲 executor 在运行。
- **COOLDOWN**：冷却期内，不主动调仓。
- **SUSPENDED**：LP 或风控条件不允许对冲。

### 5.2 LP 状态门禁
- 仅当 LP 状态为 `IN_RANGE` 或 `OUT_OF_RANGE` 时允许对冲。
- `OPENING/CLOSING` 或无 LP 时，视为“无有效 LP”，对冲进入 SUSPENDED 或仅允许平仓。

## 6. 对冲执行策略

### 6.1 小额对冲
- 使用 `OrderExecutor` + `MARKET`。

### 6.2 大额对冲
- 使用 `TWAPExecutor` 分片执行，减少冲击。

### 6.3 单笔上限
- `hedge_max_order_quote` 用于限制单笔冲击。

## 7. 风控与边界条件

- **无价格**：直接暂停对冲。
- **低保证金**：只允许减仓，不允许开仓（`hedge_min_available_balance_quote`）。
- **无 LP**：若净敞口很小则自动平仓，否则保持观望。
- **多空并存**：先平反向仓位，再开新仓，避免保证金浪费。
- **限频**：`hedge_max_per_hour` 限制调仓频率。

## 8. 配置要点（节选）

- `hedge_ratio`：部分对冲强度（建议 0.6–0.9）。
- `hedge_price_type`：对冲价格源（MidPrice/BestBid/BestAsk）。
- `hedge_delta_band_quote` / `hedge_delta_band_pct`：带宽与动态带宽。
- `hedge_delta_hysteresis_quote`：防抖阈值。
- `hedge_rebalance_interval_seconds`：时间触发兜底。
- `hedge_use_twap_over_quote`：大额改 TWAP。
- `hedge_max_order_quote`：单笔上限。
- `hedge_min_available_balance_quote`：保证金下限（仅限制开仓）。

## 9. 生产实践建议

- **不要追求零 delta**：带宽 + 冷却 + 时间触发更稳。
- **窄区间更高 gamma**：对冲成本会上升，带宽需更宽。
- **资金安全优先**：宁可少赚，不要触发强平。
- **先小额验证**：用最小预算校准 band 与 interval。

## 10. 限制与风险说明

- 对冲只能消除一阶风险，无法对冲 gamma。
- DEX 价格与 CEX/Perp 价格不一致会带来 basis 风险。
- funding 长期不利时会侵蚀收益。

---

如需扩展为“LP + 对冲 + XEMM/Arb”组合，可在 `bots/scripts/v2_with_controllers.py` 中加载多个 controller，
同时设置预算隔离与 DEX 动作互斥门禁，避免链上动作冲突。
