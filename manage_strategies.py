#!/usr/bin/env python3
"""
Gateway LP 多策略管理脚本 (通用版)

自动扫描 bots/conf/scripts/ 下的所有配置文件，无需手动添加代码。

使用方法:
    python manage_strategies.py list              # 列出所有可用策略
    python manage_strategies.py deploy <config>   # 部署策略 (配置文件名)
    python manage_strategies.py status            # 查看运行状态
    python manage_strategies.py stop <name>       # 停止策略
    python manage_strategies.py logs <name>       # 查看日志
    python manage_strategies.py restart-gateway   # 重启 Gateway
"""

import argparse
import asyncio
import os
import sys
import yaml
from pathlib import Path
from typing import Dict, List, Optional

try:
    import httpx
except ImportError:
    print("请先安装依赖: pip install httpx pyyaml")
    sys.exit(1)

# 配置
API_URL = os.getenv("HUMMINGBOT_API_URL", "http://localhost:8000")
API_USERNAME = os.getenv("HUMMINGBOT_API_USERNAME", "admin")
API_PASSWORD = os.getenv("HUMMINGBOT_API_PASSWORD", "admin")


class StrategyConfig:
    """策略配置"""
    def __init__(self, config_file: Path):
        self.config_file = config_file
        self.name = config_file.stem  # 文件名（不含扩展名）
        self.data = {}
        self._load()

    def _load(self):
        try:
            with open(self.config_file) as f:
                self.data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"警告: 无法加载 {self.config_file}: {e}")

    @property
    def script_file(self) -> str:
        return self.data.get("script_file_name", "").replace(".py", "")

    @property
    def trading_pair(self) -> str:
        return self.data.get("trading_pair", "N/A")

    @property
    def connector(self) -> str:
        return self.data.get("connector", "N/A")

    @property
    def quote_amount(self) -> str:
        return str(self.data.get("quote_amount", "N/A"))

    def __str__(self):
        return f"{self.name} ({self.trading_pair} on {self.connector})"


class StrategyManager:
    def __init__(self):
        self.base_path = Path(__file__).parent
        self.configs_dir = self.base_path / "bots" / "conf" / "scripts"
        self.scripts_dir = self.base_path / "bots" / "scripts"
        self.client = httpx.AsyncClient(
            base_url=API_URL,
            auth=(API_USERNAME, API_PASSWORD),
            timeout=60.0
        )

    async def close(self):
        await self.client.aclose()

    def scan_configs(self) -> List[StrategyConfig]:
        """扫描所有策略配置"""
        configs = []
        for f in sorted(self.configs_dir.glob("*.yml")):
            if f.name.startswith("."):
                continue
            config = StrategyConfig(f)
            if config.script_file:  # 只包含有效的配置
                configs.append(config)
        return configs

    def get_config(self, name: str) -> Optional[StrategyConfig]:
        """根据名称获取配置"""
        # 支持带或不带 .yml 后缀
        if not name.endswith(".yml"):
            name = f"{name}.yml"

        config_path = self.configs_dir / name
        if config_path.exists():
            return StrategyConfig(config_path)
        return None

    async def list_configs(self):
        """列出所有可用的策略配置"""
        configs = self.scan_configs()

        print("\n" + "=" * 70)
        print("可用的 Gateway LP 策略配置")
        print("=" * 70)

        if not configs:
            print("  (未找到策略配置)")
            return

        # 按连接器分组
        by_connector: Dict[str, List[StrategyConfig]] = {}
        for config in configs:
            connector = config.connector
            if connector not in by_connector:
                by_connector[connector] = []
            by_connector[connector].append(config)

        for connector, group in by_connector.items():
            print(f"\n【{connector}】")
            print("-" * 60)
            for config in group:
                print(f"  {config.name}")
                print(f"      交易对: {config.trading_pair}")
                print(f"      脚本: {config.script_file}.py")
                print(f"      预算: {config.quote_amount}")
                print()

        print("=" * 70)
        print("部署命令: python manage_strategies.py deploy <配置名>")
        print("例如: python manage_strategies.py deploy v2_meteora_tomato_sol")
        print("=" * 70)

    async def deploy(self, config_name: str, instance_name: str = None, credentials: str = "master_account"):
        """部署策略"""
        config = self.get_config(config_name)
        if not config:
            print(f"错误: 找不到配置 '{config_name}'")
            print(f"可用配置: {[c.name for c in self.scan_configs()]}")
            return False

        # 默认使用配置名作为实例名
        if not instance_name:
            instance_name = config.name.replace(".", "_")

        print(f"\n部署策略: {config}")
        print(f"  实例名: {instance_name}")
        print(f"  脚本: {config.script_file}")
        print(f"  配置: {config.config_file.name}")
        print(f"  凭证: {credentials}")

        try:
            response = await self.client.post(
                "/bot-orchestration/deploy-v2-script",
                json={
                    "instance_name": instance_name,
                    "credentials_profile": credentials,
                    "image": "hummingbot/hummingbot:latest",
                    "script": config.script_file,
                    "script_config": config.config_file.name,
                    "headless": True
                }
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    print(f"\n✓ 部署成功!")
                    print(f"  容器: hummingbot-{instance_name}")
                    print(f"\n查看日志: python manage_strategies.py logs {instance_name}")
                    return True
                else:
                    print(f"\n✗ 部署失败: {result.get('message', 'Unknown error')}")
                    return False
            else:
                print(f"\n✗ HTTP 错误 {response.status_code}: {response.text}")
                return False
        except Exception as e:
            print(f"\n✗ 错误: {e}")
            return False

    async def status(self):
        """查看所有运行中的实例"""
        print("\n" + "=" * 70)
        print("运行状态")
        print("=" * 70)

        try:
            # 获取 Bot 状态
            response = await self.client.get("/bot-orchestration/status")
            if response.status_code == 200:
                result = response.json()
                bots = result.get("data", {})

                print("\n【Bot 实例】")
                print("-" * 60)
                if not bots:
                    print("  (无运行中的 Bot)")
                else:
                    for bot_name, status in bots.items():
                        state = status.get('status', 'unknown')
                        strategy = status.get('strategy', 'N/A')
                        print(f"  {bot_name}")
                        print(f"      状态: {state}")
                        if strategy != 'N/A':
                            print(f"      策略: {strategy}")

            # 获取 Docker 容器状态
            response = await self.client.get("/docker/containers/active", params={"name_filter": "hummingbot"})
            if response.status_code == 200:
                containers = response.json()
                print("\n【Docker 容器】")
                print("-" * 60)
                if not containers:
                    print("  (无运行中的 hummingbot 容器)")
                else:
                    for container in containers:
                        name = container.get('name', 'unknown')
                        status = container.get('status', 'unknown')
                        image = container.get('image', 'unknown')
                        print(f"  {name}: {status} ({image})")

            # Gateway 状态
            response = await self.client.get("/docker/containers/active", params={"name_filter": "gateway"})
            if response.status_code == 200:
                containers = response.json()
                print("\n【Gateway】")
                print("-" * 60)
                if containers:
                    for container in containers:
                        print(f"  {container.get('name')}: {container.get('status')}")
                else:
                    print("  ⚠ Gateway 未运行!")

        except Exception as e:
            print(f"错误: {e}")

        print("\n" + "=" * 70)

    async def stop(self, instance_name: str):
        """停止策略"""
        print(f"正在停止: {instance_name}")
        try:
            response = await self.client.post(
                "/bot-orchestration/stop-bot",
                json={
                    "bot_name": instance_name,
                    "skip_order_cancellation": False,
                    "async_backend": True
                }
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "success":
                    print(f"✓ 已发送停止命令")
                else:
                    print(f"⚠ {result}")
            else:
                print(f"✗ 停止失败: {response.text}")
        except Exception as e:
            print(f"✗ 错误: {e}")

    async def stop_all(self):
        """停止所有实例"""
        try:
            response = await self.client.get("/bot-orchestration/status")
            if response.status_code == 200:
                result = response.json()
                bots = result.get("data", {})
                if not bots:
                    print("没有运行中的 Bot")
                    return
                for bot_name in bots.keys():
                    await self.stop(bot_name)
                    await asyncio.sleep(1)
        except Exception as e:
            print(f"错误: {e}")

    async def logs(self, instance_name: str, lines: int = 100):
        """查看实例日志"""
        # 优先查看文件日志
        log_dir = self.base_path / "bots" / "instances" / instance_name / "logs"
        if log_dir.exists():
            log_files = list(log_dir.glob("*.log"))
            if log_files:
                # 找最新的日志文件
                latest_log = max(log_files, key=lambda f: f.stat().st_mtime)
                print(f"\n日志文件: {latest_log}")
                print("-" * 70)
                with open(latest_log) as f:
                    all_lines = f.readlines()
                    for line in all_lines[-lines:]:
                        print(line, end="")
                return

        # 回退到 Docker 日志
        try:
            container_name = f"hummingbot-{instance_name}"
            response = await self.client.get(
                f"/docker/containers/{container_name}/logs",
                params={"tail": lines}
            )
            if response.status_code == 200:
                print(f"\n{container_name} 最近 {lines} 行日志:")
                print("-" * 70)
                print(response.text)
            else:
                print(f"获取日志失败: {response.text}")
        except Exception as e:
            print(f"错误: {e}")

    async def restart_gateway(self):
        """重启 Gateway"""
        print("正在重启 Gateway...")
        try:
            # 使用 Docker API
            response = await self.client.post("/docker/containers/gateway/restart")
            if response.status_code == 200:
                print("✓ Gateway 重启成功")
            else:
                # 备用方案：直接用 docker 命令
                import subprocess
                subprocess.run(["docker", "restart", "gateway"], check=True)
                print("✓ Gateway 重启成功")
        except Exception as e:
            print(f"使用 docker 命令重启...")
            import subprocess
            try:
                subprocess.run(["docker", "restart", "gateway"], check=True)
                print("✓ Gateway 重启成功")
            except:
                print(f"✗ 重启失败: {e}")
                print("请手动执行: docker restart gateway")


async def main():
    parser = argparse.ArgumentParser(
        description="Gateway LP 多策略管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python manage_strategies.py list                    # 列出所有策略
  python manage_strategies.py deploy v2_meteora_tomato_sol   # 部署策略
  python manage_strategies.py deploy v2_meteora_tomato_sol --name my_bot  # 自定义实例名
  python manage_strategies.py status                  # 查看状态
  python manage_strategies.py logs v2_meteora_tomato_sol     # 查看日志
  python manage_strategies.py stop v2_meteora_tomato_sol     # 停止策略
  python manage_strategies.py restart-gateway         # 重启 Gateway
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # list
    subparsers.add_parser("list", help="列出所有可用策略")

    # deploy
    deploy_p = subparsers.add_parser("deploy", help="部署策略")
    deploy_p.add_argument("config", help="配置文件名 (不需要 .yml 后缀)")
    deploy_p.add_argument("--name", help="自定义实例名 (默认使用配置文件名)")
    deploy_p.add_argument("--credentials", default="master_account", help="凭证配置")

    # status
    subparsers.add_parser("status", help="查看运行状态")

    # stop
    stop_p = subparsers.add_parser("stop", help="停止策略")
    stop_p.add_argument("name", help="实例名称")

    # stop-all
    subparsers.add_parser("stop-all", help="停止所有策略")

    # logs
    logs_p = subparsers.add_parser("logs", help="查看日志")
    logs_p.add_argument("name", help="实例名称")
    logs_p.add_argument("--lines", "-n", type=int, default=100, help="显示行数")

    # restart-gateway
    subparsers.add_parser("restart-gateway", help="重启 Gateway")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    manager = StrategyManager()

    try:
        if args.command == "list":
            await manager.list_configs()
        elif args.command == "deploy":
            await manager.deploy(args.config, args.name, args.credentials)
        elif args.command == "status":
            await manager.status()
        elif args.command == "stop":
            await manager.stop(args.name)
        elif args.command == "stop-all":
            await manager.stop_all()
        elif args.command == "logs":
            await manager.logs(args.name, args.lines)
        elif args.command == "restart-gateway":
            await manager.restart_gateway()
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
