# Gateway LP 策略多实例管理指南

本指南说明如何使用 hummingbot-api 管理多个 Gateway LP 策略。

## 重要说明

**Dashboard 不直接支持 Gateway LP 自定义脚本**，但 **API 完全支持**！

- Dashboard 主要用于 CEX 的 V2 Controllers 策略
- Gateway LP 脚本需要通过 API 或命令行工具部署
- 本指南提供了 `manage_strategies.py` 脚本来简化操作

## 目录结构

```
hummingbot-api/
├── bots/
│   ├── scripts/                    # 策略脚本 ⬅️ 你的 LP 脚本
│   │   ├── v2_meteora_clmm_lp_guarded.py
│   │   ├── v2_clmm_lp_recenter.py
│   │   ├── lp_manage_position.py
│   │   └── lp/                     # LP 模块
│   │       ├── budget_manager.py
│   │       ├── lp_position_manager.py
│   │       └── ...
│   ├── conf/
│   │   └── scripts/                # 策略配置 ⬅️ 你的 YAML 配置
│   │       ├── v2_meteora_tomato_sol.yml
│   │       ├── v2_meteora_sol_usdc.yml
│   │       └── ...
│   ├── credentials/                # 凭证（API keys + conf_client.yml）
│   │   └── master_account/
│   │       └── conf_client.yml     # ⬅️ 已配置 Gateway 连接
│   └── instances/                  # 运行实例（自动生成）
├── gateway-files/                  # Gateway 配置 ⬅️ 你的 Gateway 配置
│   └── conf/
│       ├── chains/solana.yml
│       ├── connectors/
│       └── wallets/
├── docker-compose.yml              # ⬅️ 已添加 Gateway 服务
├── manage_strategies.py            # ⬅️ 策略管理脚本
└── .env
```

## 快速开始

### 1. 启动服务

```bash
cd /Users/qinghuan/Documents/code/hummingbot-api

# 启动所有服务
docker compose up -d

# 查看服务状态
docker compose ps
```

启动后可访问:
| 服务 | 地址 | 说明 |
|------|------|------|
| API | http://localhost:8000 | REST API |
| API Docs | http://localhost:8000/docs | Swagger 文档 |
| Dashboard | http://localhost:8501 | Web 管理界面 (CEX 策略) |
| Gateway | http://localhost:15888 | Gateway API |
| EMQX | http://localhost:18083 | MQTT 管理 (admin/public) |

### 2. 设置 .env 密码

确保 `.env` 中的 `CONFIG_PASSWORD` 已设置:
```bash
# 查看当前配置
cat .env

# 如需修改
nano .env
```

### 3. 部署 Gateway LP 策略

**方式 A: 使用管理脚本 (推荐)**

```bash
# 安装依赖
pip install httpx pyyaml

# 查看可用策略
python manage_strategies.py list

# 部署单个策略
python manage_strategies.py deploy --name tomato_lp --config v2_meteora_tomato_sol.yml

# 部署多个策略 (修改 STRATEGIES 变量后)
python manage_strategies.py deploy-all

# 查看状态
python manage_strategies.py status

# 查看日志
python manage_strategies.py logs --name tomato_lp

# 停止策略
python manage_strategies.py stop --name tomato_lp
```

**方式 B: 使用 API (curl)**

```bash
# 部署策略
curl -X POST http://localhost:8000/bot-orchestration/deploy-v2-script \
  -u admin:admin \
  -H "Content-Type: application/json" \
  -d '{
    "instance_name": "tomato_lp",
    "credentials_profile": "master_account",
    "image": "hummingbot/hummingbot:latest",
    "script": "v2_meteora_clmm_lp_guarded",
    "script_config": "v2_meteora_tomato_sol.yml",
    "headless": true
  }'

# 查看状态
curl -u admin:admin http://localhost:8000/bot-orchestration/status

# 停止策略
curl -X POST http://localhost:8000/bot-orchestration/stop-bot \
  -u admin:admin \
  -H "Content-Type: application/json" \
  -d '{"bot_name": "tomato_lp", "skip_order_cancellation": false}'
```

**方式 C: 使用 Swagger UI**

1. 打开 http://localhost:8000/docs
2. 点击 "Authorize" 输入 admin/admin
3. 找到 `/bot-orchestration/deploy-v2-script`
4. 填写参数并执行

## 添加新策略

### 1. 创建配置文件

在 `bots/conf/scripts/` 创建新的 YAML:

```yaml
# bots/conf/scripts/v2_new_pool.yml
script_file_name: v2_meteora_clmm_lp_guarded.py

connector: meteora/clmm
router_connector: jupiter/router
price_source: pool_info

trading_pair: NEW-SOL
pool_address: <your_pool_address>

base_amount: 0
quote_amount: 1.0
position_width_pct: 20
# ... 其他参数
```

### 2. 部署

```bash
python manage_strategies.py deploy --name new_pool_lp --config v2_new_pool.yml
```

## 多策略并行运行

要同时运行多个策略，只需部署多个实例:

```bash
# 策略 1: TOMATO-SOL
python manage_strategies.py deploy --name tomato_lp --config v2_meteora_tomato_sol.yml

# 策略 2: SOL-USDC
python manage_strategies.py deploy --name sol_usdc_lp --config v2_meteora_sol_usdc.yml

# 策略 3: 其他池
python manage_strategies.py deploy --name other_lp --config v2_other_pool.yml
```

每个实例运行在独立的 Docker 容器中。

## Gateway 配置

Gateway 配置位于 `gateway-files/conf/`:

### 修改 Solana RPC

```bash
# 编辑 Solana 网络配置
nano gateway-files/conf/chains/solana/mainnet-beta.yml
```

```yaml
nodeUrl: https://your-helius-or-other-rpc.com
```

### 查看钱包

```bash
ls gateway-files/conf/wallets/solana/
```

## 故障排除

### Gateway 连接失败

1. 确认 Gateway 运行中: `docker ps | grep gateway`
2. 检查 Gateway 日志: `docker logs gateway`
3. 确认 `conf_client.yml` 中 Gateway 地址正确:
   ```yaml
   gateway:
     gateway_api_host: host.docker.internal
     gateway_api_port: '15888'
   ```

### Bot 无法连接 MQTT

1. 确认 EMQX 运行中: `docker ps | grep emqx`
2. 检查 `conf_client.yml` 中 MQTT 配置:
   ```yaml
   mqtt_bridge:
     mqtt_host: host.docker.internal
     mqtt_port: 1883
   ```

### 查看 Bot 日志

```bash
# 通过 Docker
docker logs -f hummingbot-tomato_lp

# 或查看文件
tail -f bots/instances/tomato_lp/logs/*.log
```

### 策略无法启动

1. 检查配置文件路径是否正确
2. 检查脚本文件是否存在于 `bots/scripts/`
3. 检查 `script_file_name` 是否正确
4. 查看 API 日志: `docker logs hummingbot-api`

## API 端点参考

| 端点 | 方法 | 说明 |
|------|------|------|
| `/bot-orchestration/deploy-v2-script` | POST | 部署脚本策略 |
| `/bot-orchestration/status` | GET | 获取所有 Bot 状态 |
| `/bot-orchestration/{bot_name}/status` | GET | 获取单个 Bot 状态 |
| `/bot-orchestration/start-bot` | POST | 启动已存在的 Bot |
| `/bot-orchestration/stop-bot` | POST | 停止 Bot |
| `/scripts/` | GET | 列出所有脚本 |
| `/scripts/configs/` | GET | 列出所有脚本配置 |
| `/docker/containers/active` | GET | 列出运行中的容器 |

## 停止服务

```bash
# 停止所有服务
docker compose down

# 停止并清除数据
docker compose down -v
```
