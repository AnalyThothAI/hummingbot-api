# 外部元数据集成设计（GeckoTerminal + Meteora）

## 0. 修改前说明

- 问题：Dashboard 的 Add Token / Add Pool 需要手填，无法自动补全 decimals/name/symbol，也缺少 CLMM 池子检索能力。
- 本质：缺少与 Gateway 解耦的外部元数据聚合层（Token + CLMM Pools）与限流/缓存。
- 影响范围：新增后端 `metadata` 路由与服务、Dashboard 交互增强；Gateway 逻辑不变。
- 验收方式：BSC/Solana 合约可回填 token 信息；CLMM pools 返回 volume/APR/APY；无 API key 时可用且不过频。

## 1. 目标与原则

- **解耦**：元数据查询独立于 Gateway，不依赖 connector 或交易逻辑。
- **KISS/YAGNI**：仅覆盖当前需求（token 元数据 + CLMM pools）；不做 AMM、不做自动刷新任务。
- **DRY**：统一 Provider 接口与字段映射；缓存/限流共享。
- **免费优先**：不要求 API key；控制调用频率，优先命中缓存。
- **稳定优先**：外部服务失败时可退回手填，避免阻塞主流程。

## 2. 范围与不做

**范围**
- Token：`decimals` / `name` / `symbol` / `address`（按 network+address 查询）
- Pools：CLMM 池子列表（交易对、地址、费率、TVL、Volume、APR/APY）
- 目标链：BSC（主要）、Solana；其他链若 GeckoTerminal 支持则自动兼容

**不做**
- AMM 池子列表
- 持久化存储（仅内存缓存）
- 后台定时抓取
- 交易决策或与 Gateway 状态耦合

## 3. 架构与职责

```
Dashboard (Streamlit)
   -> /metadata/token
   -> /metadata/pools
          |
          v
routers/metadata.py  (请求校验、返回标准化响应)
services/external_data/
  - providers/*.py   (GeckoTerminal, Meteora)
  - cache.py         (TTL/LRU, 负缓存)
  - limiter.py       (简单限流)
```

**职责分离**
- Router：协议与错误码统一
- Provider：对外 API 调用与字段映射
- Cache/Limiter：共用基础能力

## 4. API 设计（对 Dashboard）

### 4.1 Token 元数据
`GET /metadata/token?network_id=...&address=...`

响应示例：
```json
{
  "ok": true,
  "source": "geckoterminal",
  "cached": true,
  "rate_limited": false,
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

### 4.2 Pools 列表（CLMM）
`GET /metadata/pools?connector=...&network_id=...&page=0&limit=50&search=...`

响应示例：
```json
{
  "ok": true,
  "source": "meteora",
  "cached": false,
  "rate_limited": false,
  "total": 1234,
  "pools": [
    {
      "address": "...",
      "trading_pair": "SOL-USDC",
      "base_address": "...",
      "quote_address": "...",
      "fee_tier": "0.30",
      "volume_24h": "123456.78",
      "tvl_usd": "987654.32",
      "apr": "0.28",
      "apy": "0.32",
      "pool_type": "clmm",
      "network_id": "solana-mainnet-beta"
    }
  ],
  "warnings": []
}
```

**通用错误返回**
```json
{
  "ok": false,
  "error": "rate_limited",
  "message": "Upstream rate limited",
  "retry_after": 30
}
```

## 5. Provider 与数据源

### 5.1 GeckoTerminal（无 key）
- **Token**：`/api/v2/networks/{network}/tokens/{address}`
  - 取 `attributes.name/symbol/decimals`
- **Pools（CLMM）**：`/api/v2/networks/{network}/dexes/{dex}/pools`
  - `dex`: `uniswap-v3`、`pancakeswap-v3`
  - 取 `attributes.volume_usd.h24` / `attributes.fees_usd.h24` / `attributes.reserve_in_usd`
  - 若 `apr/apy` 未提供：使用 `fees_24h / tvl_usd` 估算

**注意**
- 统一返回 `volume_24h`, `tvl_usd`, `apr`, `apy`（字符串形式，便于 Decimal）
- 没有数据时返回空列表，避免抛错阻断 UI

### 5.2 Meteora（Solana CLMM）
- 使用官方 API：`https://dlmm-api.meteora.ag/pair/all_by_groups`
- 取 `trade_volume_24h`、`apr/apy`、`liquidity` 等
- 直接转换为统一字段（`pool_type=clmm`）

## 6. 网络映射

| network_id | GeckoTerminal network |
| --- | --- |
| bsc-mainnet / binance-smart-chain-mainnet | bsc |
| solana-mainnet-beta | solana |
| ethereum-mainnet | ethereum |
| arbitrum-mainnet | arbitrum |
| base-mainnet | base |
| polygon-mainnet | polygon |

> 非映射网络直接返回 `not_supported`，UI 提示手填。

## 7. 缓存与限流

**缓存（内存 LRU + TTL）**
- token：TTL=24h，负缓存=10min，LRU=5000
- pools：TTL=2-5min，LRU=1000

**限流（按 provider）**
- 默认 1 req/sec
- 429 时读取 `Retry-After`
- 若命中缓存则返回 `rate_limited=true` 并给出缓存数据

## 8. Dashboard 交互变更

- Add Token：新增“查询 Token 信息”按钮，成功则回填 `symbol/name/decimals`
- Add Pool：新增“搜索 CLMM Pools”表格，选择后回填地址/币对/费率
- 手填永远可用，不依赖外部 API

## 9. 实现细节（建议模块）

```
routers/metadata.py
services/external_data/
  __init__.py
  cache.py
  limiter.py
  providers/
    geckoterminal.py
    meteora.py
  mapper.py
models/metadata.py
```

**核心流程**
1) Router 校验参数 -> 映射 network
2) Cache hit 直接返回
3) Limiter 通过后调用 provider
4) 规范化字段 -> cache -> return

## 10. 风险与回滚

**风险**
- 外部 API 变更/限流导致数据缺失
- 无 key 限额较低

**回滚**
- 关闭 metadata 路由或隐藏 Dashboard 查询按钮
- 不影响 Gateway 现有 add token/pool 能力

## 11. 验收标准（最小可验证）

- `GET /metadata/token` 在 BSC/Solana 上返回 decimals/name/symbol
- `GET /metadata/pools` 返回 CLMM pools，包含 volume/apr/apy
- 429 时返回 `rate_limited`，缓存可用
- Dashboard 可一键填充，手填流程不受影响

## 12. 原子任务拆分

1) 新增 `routers/metadata.py` + `services/external_data/*` 基础结构与模型
2) 实现 GeckoTerminal token + pools
3) 迁移/复用 Meteora pools 逻辑
4) Dashboard 增加查询/填充交互
5) 文档/配置说明补充
