# 外部元数据集成设计（复用 Gateway）

## 0. 修改前说明

- 问题：Dashboard 的 Add Token / Add Pool 需要手填，缺少自动补全与搜索能力。
- 本质：复用 Gateway 已有的 GeckoTerminal / Meteora 查询能力，避免新增独立 Provider 与缓存。
- 影响范围：新增后端 `metadata` 路由、Dashboard 交互增强；Gateway 逻辑不变。
- 验收方式：BSC/Solana 可回填 token 信息；CLMM pools 可搜索并回填地址/币对/费率；手填流程不受影响。

## 1. 目标与原则

- **解耦**：Dashboard 只依赖 `/metadata/*`，不直接调用 Gateway 或外部 API。
- **KISS/YAGNI**：不新增 provider/cache/定时任务。
- **DRY**：字段映射集中在 metadata 路由。
- **稳定优先**：失败时 UI 允许手填，不阻断主流程。

## 2. 范围与不做

**范围**
- Token：`decimals` / `name` / `symbol` / `address`（按 network+address 查询）
- Pools：CLMM/AMM 列表（交易对、地址、费率、TVL、Volume、APR/APY 可估算）
- 目标链：由 Gateway 配置支持的网络

**不做**
- 新增外部数据直连（全部经 Gateway）
- 新增缓存与限流
- 后台定时抓取

## 3. 架构与职责

```
Dashboard (Streamlit)
   -> /metadata/token
   -> /metadata/pools
          |
          v
routers/metadata.py  (请求校验、字段映射、轻量计算)
          |
          v
Gateway REST
  - /tokens/find
  - /pools/find
          |
          v
GeckoTerminal / Meteora (由 Gateway 内部调用)
```

**职责分离**
- Router：参数校验 + 统一响应
- Gateway：对外 API 调用与数据聚合

## 4. API 设计（对 Dashboard）

### 4.1 Token 元数据
`GET /metadata/token?network_id=...&address=...`

响应示例：
```json
{
  "ok": true,
  "source": "gateway",
  "token": {
    "network_id": "bsc-mainnet",
    "address": "0x...",
    "symbol": "USDC",
    "name": "USD Coin",
    "decimals": 6
  },
  "warnings": []
}
```

### 4.2 Pools 列表
`GET /metadata/pools?network_id=...&connector=...&pool_type=clmm&token_a=...&token_b=...&search=...&pages=1&limit=50`

响应示例：
```json
{
  "ok": true,
  "source": "gateway",
  "total": 2,
  "pools": [
    {
      "address": "...",
      "trading_pair": "SOL-USDC",
      "base_symbol": "SOL",
      "quote_symbol": "USDC",
      "base_address": "...",
      "quote_address": "...",
      "fee_tier": "0.30",
      "bin_step": 10,
      "volume_24h": "123456.78",
      "tvl_usd": "987654.32",
      "apr": "12.34",
      "apy": "13.15",
      "pool_type": "clmm",
      "connector": "meteora",
      "network_id": "solana-mainnet-beta"
    }
  ],
  "warnings": []
}
```

**错误返回**
- 使用 HTTP 状态码（400/502/503），`detail` 提示错误原因。

## 5. 数据源与 Gateway 端接口

**Gateway 接口（metadata 直接调用）**
- `GET /tokens/find/:address?chainNetwork=...`
- `GET /pools/find?chainNetwork=...&connector=...&type=...&tokenA=...&tokenB=...&pages=...`

**上游官方 API（由 Gateway 内部调用）**
- GeckoTerminal：`/api/v2/networks/{network}/tokens/{address}`、`/api/v2/networks/{network}/pools` 等
- Meteora DLMM：`https://dlmm-api.meteora.ag/pair/all_by_groups`

> metadata 层不直连外部 API，统一经 Gateway。

## 6. 网络映射

- network_id 与 GeckoTerminal 的映射由 Gateway 的 `geckoId` 配置决定。
- 未配置的 network_id 会在 Gateway 层报错，metadata 返回 HTTP 错误提示。

## 7. 缓存与限流

- metadata 层不做缓存/限流。
- 依赖 Gateway 与上游服务自身限制策略。

## 8. Dashboard 交互变更

- Add Token：填写 network + address 后自动回填 `symbol/name/decimals`。
- Add Pool：输入 token/search 后自动搜索并展示池列表，选择后回填地址/币对/费率；若为 Meteora，展示 `bin_step`。
- 手填流程不受影响。

## 9. 实现细节（建议模块）

```
routers/metadata.py
services/gateway_client.py   (find_token / find_pools)
main.py                      (注册 metadata router)
Dashboard gateway 页面        (查询与回填)
```

## 10. 风险与回滚

**风险**
- Gateway 或上游 API 变更导致字段缺失

**回滚**
- 关闭 metadata 路由或隐藏 Dashboard 查询按钮

## 11. 验收标准（最小可验证）

- `GET /metadata/token` 返回 decimals/name/symbol
- `GET /metadata/pools` 返回 pools 列表（含交易对/地址/费率，APR/APY 可估算）
- Dashboard 可一键填充，手填流程不受影响

## 12. 原子任务拆分

1) 新增 `routers/metadata.py` 与 `GatewayClient` 方法
2) Dashboard 增加查询与回填交互
3) 文档更新
