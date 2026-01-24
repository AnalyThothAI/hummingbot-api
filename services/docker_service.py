import logging
import os
import platform
import shutil
import time
import threading
from urllib.parse import urlparse
from typing import Dict, Optional

# Create module-specific logger
logger = logging.getLogger(__name__)

import docker
from docker.errors import DockerException, NotFound
from docker.types import LogConfig

from config import settings
from models import V2ScriptDeployment
from utils.file_system import fs_util


class DockerService:
    # Class-level configuration for cleanup
    PULL_STATUS_MAX_AGE_SECONDS = 3600  # Keep status for 1 hour
    PULL_STATUS_MAX_ENTRIES = 100  # Maximum number of entries to keep
    CLEANUP_INTERVAL_SECONDS = 300  # Run cleanup every 5 minutes
    
    def __init__(self):
        self.SOURCE_PATH = os.getcwd()
        self._pull_status: Dict[str, Dict] = {}
        self._cleanup_thread = None
        self._stop_cleanup = threading.Event()
        
        try:
            self.client = docker.from_env()
            # Start background cleanup thread
            self._start_cleanup_thread()
        except DockerException as e:
            logger.error(f"It was not possible to connect to Docker. Please make sure Docker is running. Error: {e}")

    def _resolve_bot_network_mode(self):
        system_platform = platform.system()
        in_container = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
        use_host_network = (
            settings.bot_deployment.use_host_network_linux
            and system_platform == "Linux"
            and not in_container
        )
        return use_host_network, system_platform, in_container

    @staticmethod
    def _normalize_bot_host(current_host: str, use_host_network: bool, bridge_default: str) -> str:
        if not current_host:
            current_host = ""

        local_hosts = {"localhost", "127.0.0.1", "host.docker.internal"}
        bridge_service_hosts = {"emqx", "gateway"}

        if use_host_network:
            if current_host in local_hosts or current_host in bridge_service_hosts:
                return "localhost"
            return current_host

        if current_host in local_hosts or current_host == "":
            return bridge_default
        return current_host

    def _resolve_bot_hosts(self, client_config: dict, use_host_network: bool) -> Dict[str, str]:
        mqtt_bridge = client_config.get("mqtt_bridge") or {}
        current_mqtt_host = mqtt_bridge.get("mqtt_host")
        default_mqtt_host = settings.bot_deployment.mqtt_host or "emqx"
        mqtt_host = self._normalize_bot_host(current_mqtt_host, use_host_network, default_mqtt_host)

        gateway_cfg = client_config.get("gateway") or {}
        current_gateway_host = gateway_cfg.get("gateway_api_host")
        if not current_gateway_host:
            try:
                current_gateway_host = urlparse(settings.gateway.url).hostname
            except Exception:
                current_gateway_host = None
        default_gateway_host = settings.bot_deployment.gateway_host or "gateway"
        gateway_host = self._normalize_bot_host(current_gateway_host, use_host_network, default_gateway_host)

        return {"mqtt_host": mqtt_host, "gateway_host": gateway_host}

    def _get_bot_networks(self) -> list:
        raw_networks = settings.bot_deployment.networks or ""
        return [name.strip() for name in raw_networks.split(",") if name.strip()]

    def _connect_to_bot_network(self, container):
        possible_networks = self._get_bot_networks()
        if not possible_networks:
            logger.info("No bot networks configured; skipping network attachment")
            return False
        for net in possible_networks:
            try:
                network = self.client.networks.get(net)
                network.connect(container)
                logger.info(f"Connected bot container to {net} network")
                return True
            except docker.errors.NotFound:
                continue
            except DockerException as e:
                logger.warning(f"Failed to connect bot container to {net}: {e}")
                return False
        logger.warning("No emqx-bridge network found for bot container")
        return False

    def _safe_container_image_label(self, container) -> str:
        try:
            tags = container.image.tags
            if tags:
                return tags[0]
            image_id = getattr(container.image, "id", None)
            if image_id:
                return image_id[:12]
        except (NotFound, DockerException):
            pass

        try:
            image_id = container.attrs.get("Image")
            if image_id:
                return image_id[:12]
        except Exception:
            pass

        return "unknown"

    def get_active_containers(self, name_filter: str = None):
        try:
            all_containers = self.client.containers.list(filters={"status": "running"})
        except DockerException as e:
            return str(e)

        containers_info = []
        for container in all_containers:
            if name_filter and name_filter.lower() not in container.name.lower():
                continue
            containers_info.append(
                {
                    "id": container.id,
                    "name": container.name,
                    "status": container.status,
                    "image": self._safe_container_image_label(container),
                }
            )
        return containers_info

    def get_available_images(self):
        try:
            images = self.client.images.list()
            return {"images": images}
        except DockerException as e:
            return str(e)

    def pull_image(self, image_name):
        try:
            return self.client.images.pull(image_name)
        except DockerException as e:
            return str(e)

    def pull_image_sync(self, image_name):
        """Synchronous pull operation for background tasks"""
        try:
            result = self.client.images.pull(image_name)
            return {"success": True, "image": image_name, "result": str(result)}
        except DockerException as e:
            return {"success": False, "error": str(e)}

    def get_exited_containers(self, name_filter: str = None):
        try:
            all_containers = self.client.containers.list(filters={"status": "exited"}, all=True)
        except DockerException as e:
            return str(e)

        containers_info = []
        for container in all_containers:
            if name_filter and name_filter.lower() not in container.name.lower():
                continue
            containers_info.append(
                {
                    "id": container.id,
                    "name": container.name,
                    "status": container.status,
                    "image": self._safe_container_image_label(container),
                }
            )
        return containers_info

    def clean_exited_containers(self):
        try:
            self.client.containers.prune()
        except DockerException as e:
            return str(e)

    def is_docker_running(self):
        try:
            self.client.ping()
            return True
        except DockerException:
            return False

    def stop_container(self, container_name):
        try:
            container = self.client.containers.get(container_name)
            container.stop()
        except DockerException as e:
            return str(e)

    def start_container(self, container_name):
        try:
            container = self.client.containers.get(container_name)
            container.start()
        except DockerException as e:
            return str(e)

    def get_container_status(self, container_name):
        """Get the status of a container"""
        try:
            container = self.client.containers.get(container_name)
            return {
                "success": True,
                "state": {
                    "status": container.status,
                    "running": container.status == "running",
                    "exit_code": getattr(container.attrs.get("State", {}), "ExitCode", None)
                }
            }
        except DockerException as e:
            return {"success": False, "message": str(e)}

    def get_container_logs(self, container_name: str, tail: int = 100):
        """Get container logs with timestamps."""
        try:
            container = self.client.containers.get(container_name)
        except NotFound:
            return {
                "success": False,
                "message": f"Container {container_name} not found",
                "error_type": "not_found",
            }
        except DockerException as e:
            return {"success": False, "message": str(e)}

        try:
            logs = container.logs(tail=tail, timestamps=True).decode("utf-8")
            return {
                "success": True,
                "container": container_name,
                "logs": logs,
            }
        except DockerException as e:
            return {"success": False, "message": str(e)}

    def remove_container(self, container_name, force=True):
        try:
            container = self.client.containers.get(container_name)
            container.remove(force=force)
            return {"success": True, "message": f"Container {container_name} removed successfully."}
        except DockerException as e:
            return {"success": False, "message": str(e)}

    def _write_gateway_connector_config(
        self,
        instance_name: str,
        network_id: Optional[str],
        wallet_address: Optional[str],
        connector_name: str = "uniswap",
    ) -> None:
        if not network_id:
            return
        if "-" not in network_id:
            logger.warning(f"Invalid gateway network_id format: {network_id}")
            return
        chain, network = network_id.split("-", 1)
        config_path = f"instances/{instance_name}/conf/connectors/_gateway_{connector_name}.yml"
        try:
            existing = fs_util.read_yaml_file(config_path)
            if not isinstance(existing, dict):
                existing = {}
        except FileNotFoundError:
            existing = {}
        except Exception as exc:
            logger.warning(f"Failed reading connector config {config_path}: {exc}")
            existing = {}

        existing.setdefault("connector", connector_name)
        existing["chain"] = chain
        existing["network"] = network
        if wallet_address:
            existing["wallet_address"] = wallet_address
        fs_util.dump_dict_to_yaml(config_path, existing)

    def _write_gateway_connector_configs(
        self,
        instance_name: str,
        network_id: Optional[str],
        wallet_address: Optional[str],
        connector_names: list[str],
    ) -> None:
        for connector_name in connector_names:
            if connector_name:
                self._write_gateway_connector_config(
                    instance_name=instance_name,
                    network_id=network_id,
                    wallet_address=wallet_address,
                    connector_name=connector_name,
                )

    @staticmethod
    def _extract_gateway_connector_name(connector_name: Optional[str]) -> Optional[str]:
        if not connector_name:
            return None
        return connector_name.split("/", 1)[0].strip() if "/" in connector_name else connector_name.strip()

    def create_hummingbot_instance(self, config: V2ScriptDeployment):
        bots_path = os.environ.get('BOTS_PATH', self.SOURCE_PATH)  # Default to 'SOURCE_PATH' if BOTS_PATH is not set
        instance_name = config.instance_name
        instance_dir = os.path.join("bots", 'instances', instance_name)
        use_host_network, system_platform, in_container = self._resolve_bot_network_mode()
        script_config_content = None
        if not os.path.exists(instance_dir):
            os.makedirs(instance_dir)
            os.makedirs(os.path.join(instance_dir, 'data'))
            os.makedirs(os.path.join(instance_dir, 'logs'))

        # Copy credentials to instance directory
        source_credentials_dir = os.path.join("bots", 'credentials', config.credentials_profile)
        destination_credentials_dir = os.path.join(instance_dir, 'conf')

        # Remove the destination directory if it already exists
        if os.path.exists(destination_credentials_dir):
            shutil.rmtree(destination_credentials_dir)

        # Copy the entire contents of source_credentials_dir to destination_credentials_dir     
        shutil.copytree(source_credentials_dir, destination_credentials_dir)
        
        # Copy specific script config and referenced controllers if provided
        if config.script_config:
            script_config_dir = os.path.join("bots", 'conf', 'scripts')
            controllers_config_dir = os.path.join("bots", 'conf', 'controllers')
            destination_scripts_config_dir = os.path.join(instance_dir, 'conf', 'scripts')
            destination_controllers_config_dir = os.path.join(instance_dir, 'conf', 'controllers')
            
            os.makedirs(destination_scripts_config_dir, exist_ok=True)
            
            # Copy the specific script config file
            source_script_config_file = os.path.join(script_config_dir, config.script_config)
            destination_script_config_file = os.path.join(destination_scripts_config_dir, config.script_config)
            
            if os.path.exists(source_script_config_file):
                shutil.copy2(source_script_config_file, destination_script_config_file)
                
                # Load the script config to find referenced controllers
                try:
                    # Path relative to fs_util base_path (which is "bots")
                    script_config_relative_path = f"conf/scripts/{config.script_config}"
                    script_config_content = fs_util.read_yaml_file(script_config_relative_path)
                    controllers_list = script_config_content.get('controllers_config', [])
                    
                    # If there are controllers referenced, copy them
                    if controllers_list:
                        os.makedirs(destination_controllers_config_dir, exist_ok=True)
                        
                        for controller_file in controllers_list:
                            source_controller_file = os.path.join(controllers_config_dir, controller_file)
                            destination_controller_file = os.path.join(destination_controllers_config_dir, controller_file)
                            
                            if os.path.exists(source_controller_file):
                                shutil.copy2(source_controller_file, destination_controller_file)
                                logger.info(f"Copied controller config: {controller_file}")
                            else:
                                logger.warning(f"Controller config file {controller_file} not found in {controllers_config_dir}")
                                
                except Exception as e:
                    logger.error(f"Error reading script config file {config.script_config}: {e}")
            else:
                logger.warning(f"Script config file {config.script_config} not found in {script_config_dir}")
        # Path relative to fs_util base_path (which is "bots")
        conf_file_path = f"instances/{instance_name}/conf/conf_client.yml"
        client_config = fs_util.read_yaml_file(conf_file_path)
        client_config['instance_id'] = instance_name
        resolved_hosts = self._resolve_bot_hosts(client_config, use_host_network)
        mqtt_bridge = client_config.get("mqtt_bridge") or {}
        mqtt_bridge["mqtt_host"] = resolved_hosts["mqtt_host"]
        client_config["mqtt_bridge"] = mqtt_bridge
        gateway_cfg = client_config.get("gateway") or {}
        gateway_cfg["gateway_api_host"] = resolved_hosts["gateway_host"]
        client_config["gateway"] = gateway_cfg
        fs_util.dump_dict_to_yaml(conf_file_path, client_config)

        connector_names = []
        controllers_list = []
        if isinstance(script_config_content, dict):
            connector_names.append(self._extract_gateway_connector_name(script_config_content.get("connector")))
            connector_names.append(self._extract_gateway_connector_name(script_config_content.get("router_connector")))
            connector_names.append(self._extract_gateway_connector_name(script_config_content.get("price_connector")))
            controllers_list = script_config_content.get("controllers_config", []) or []

        controller_connectors = []
        for controller_file in controllers_list:
            controller_path = f"instances/{instance_name}/conf/controllers/{controller_file}"
            try:
                controller_config = fs_util.read_yaml_file(controller_path)
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.warning(f"Failed reading controller config {controller_path}: {e}")
                continue
            if not isinstance(controller_config, dict):
                continue
            controller_connectors.append(self._extract_gateway_connector_name(controller_config.get("connector_name")))
            controller_connectors.append(self._extract_gateway_connector_name(controller_config.get("router_connector")))
            controller_connectors.append(self._extract_gateway_connector_name(controller_config.get("price_connector")))

        connector_names.extend(controller_connectors)
        connector_names = [name for name in connector_names if name]
        if not connector_names:
            connector_names = ["uniswap"]

        self._write_gateway_connector_configs(
            instance_name=instance_name,
            network_id=getattr(config, "gateway_network_id", None),
            wallet_address=getattr(config, "gateway_wallet_address", None),
            connector_names=connector_names,
        )

        # Set up Docker volumes
        volumes = {
            os.path.abspath(os.path.join(bots_path, instance_dir, 'conf')): {'bind': '/home/hummingbot/conf', 'mode': 'rw'},
            os.path.abspath(os.path.join(bots_path, instance_dir, 'conf', 'connectors')): {'bind': '/home/hummingbot/conf/connectors', 'mode': 'rw'},
            os.path.abspath(os.path.join(bots_path, instance_dir, 'conf', 'scripts')): {'bind': '/home/hummingbot/conf/scripts', 'mode': 'rw'},
            os.path.abspath(os.path.join(bots_path, instance_dir, 'conf', 'controllers')): {'bind': '/home/hummingbot/conf/controllers', 'mode': 'rw'},
            os.path.abspath(os.path.join(bots_path, instance_dir, 'data')): {'bind': '/home/hummingbot/data', 'mode': 'rw'},
            os.path.abspath(os.path.join(bots_path, instance_dir, 'logs')): {'bind': '/home/hummingbot/logs', 'mode': 'rw'},
            os.path.abspath(os.path.join(bots_path, "bots", 'scripts')): {'bind': '/home/hummingbot/scripts', 'mode': 'rw'},
            os.path.abspath(os.path.join(bots_path, "bots", 'controllers')): {'bind': '/home/hummingbot/controllers', 'mode': 'rw'},
        }

        # Set up environment variables
        environment = {}
        password = settings.secrets.config_password
        if password:
            environment["CONFIG_PASSWORD"] = password

        if config.script:
            if password:
                environment['CONFIG_FILE_NAME'] = config.script
                if config.script_config:
                    environment['SCRIPT_CONFIG'] = config.script_config
            else:
                return {"success": False, "message": "Password not provided. We cannot start the bot without a password."}

        if config.headless:
            environment["HEADLESS_MODE"] = "true"

        log_config = LogConfig(
            type="json-file",
            config={
                'max-size': '10m',
                'max-file': "5",
            })
        if use_host_network:
            logger.info("Detected native Linux - using host network mode for bot instances")
        else:
            logger.info(
                f"Detected {system_platform} (in_container={in_container}) - using bridge networking for bot instances"
            )

        try:
            container_config = {
                "image": config.image,
                "name": instance_name,
                "volumes": volumes,
                "environment": environment,
                "detach": True,
                "tty": True,
                "stdin_open": True,
                "log_config": log_config,
            }

            if use_host_network:
                container_config["network_mode"] = "host"

            container = self.client.containers.run(**container_config)

            if not use_host_network:
                self._connect_to_bot_network(container)

            return {"success": True, "message": f"Instance {instance_name} created successfully."}
        except docker.errors.DockerException as e:
            return {"success": False, "message": str(e)}

    def _start_cleanup_thread(self):
        """Start the background cleanup thread"""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._cleanup_thread = threading.Thread(target=self._periodic_cleanup, daemon=True)
            self._cleanup_thread.start()
            logger.info("Started Docker pull status cleanup thread")

    def _periodic_cleanup(self):
        """Periodically clean up old pull status entries"""
        while not self._stop_cleanup.is_set():
            try:
                self._cleanup_old_pull_status()
            except Exception as e:
                logger.error(f"Error in cleanup thread: {e}")
            
            # Wait for the next cleanup interval
            self._stop_cleanup.wait(self.CLEANUP_INTERVAL_SECONDS)

    def _cleanup_old_pull_status(self):
        """Remove old entries to prevent memory growth"""
        current_time = time.time()
        to_remove = []
        
        # Find entries older than max age
        for image_name, status_info in self._pull_status.items():
            # Skip ongoing pulls
            if status_info["status"] == "pulling":
                continue
                
            # Check age of completed/failed operations
            end_time = status_info.get("completed_at") or status_info.get("failed_at")
            if end_time and (current_time - end_time > self.PULL_STATUS_MAX_AGE_SECONDS):
                to_remove.append(image_name)
        
        # Remove old entries
        for image_name in to_remove:
            del self._pull_status[image_name]
            logger.info(f"Cleaned up old pull status for {image_name}")
        
        # If still over limit, remove oldest completed/failed entries
        if len(self._pull_status) > self.PULL_STATUS_MAX_ENTRIES:
            completed_entries = [
                (name, info) for name, info in self._pull_status.items() 
                if info["status"] in ["completed", "failed"]
            ]
            # Sort by end time (oldest first)
            completed_entries.sort(
                key=lambda x: x[1].get("completed_at") or x[1].get("failed_at") or 0
            )
            
            # Remove oldest entries to get under limit
            excess_count = len(self._pull_status) - self.PULL_STATUS_MAX_ENTRIES
            for i in range(min(excess_count, len(completed_entries))):
                del self._pull_status[completed_entries[i][0]]
                logger.info(f"Cleaned up excess pull status for {completed_entries[i][0]}")

    def pull_image_async(self, image_name: str):
        """Start pulling a Docker image asynchronously with status tracking"""
        # Check if pull is already in progress
        if image_name in self._pull_status:
            current_status = self._pull_status[image_name]
            if current_status["status"] == "pulling":
                return {
                    "message": f"Pull already in progress for {image_name}",
                    "status": "in_progress",
                    "started_at": current_status["started_at"],
                    "image_name": image_name
                }
        
        # Start the pull in a background thread
        threading.Thread(target=self._pull_image_with_tracking, args=(image_name,), daemon=True).start()
        
        return {
            "message": f"Pull started for {image_name}",
            "status": "started",
            "image_name": image_name
        }

    def _pull_image_with_tracking(self, image_name: str):
        """Background task to pull Docker image with status tracking"""
        try:
            self._pull_status[image_name] = {
                "status": "pulling", 
                "started_at": time.time(),
                "progress": "Starting pull..."
            }
            
            # Use the synchronous pull method
            result = self.pull_image_sync(image_name)
            
            if result.get("success"):
                self._pull_status[image_name] = {
                    "status": "completed", 
                    "started_at": self._pull_status[image_name]["started_at"],
                    "completed_at": time.time(),
                    "result": result
                }
            else:
                self._pull_status[image_name] = {
                    "status": "failed", 
                    "started_at": self._pull_status[image_name]["started_at"],
                    "failed_at": time.time(),
                    "error": result.get("error", "Unknown error")
                }
        except Exception as e:
            self._pull_status[image_name] = {
                "status": "failed", 
                "started_at": self._pull_status[image_name].get("started_at", time.time()),
                "failed_at": time.time(),
                "error": str(e)
            }

    def get_all_pull_status(self):
        """Get status of all pull operations"""
        operations = {}
        for image_name, status_info in self._pull_status.items():
            status_copy = status_info.copy()
            
            # Add duration for each operation
            start_time = status_copy.get("started_at")
            if start_time:
                if status_copy["status"] == "pulling":
                    status_copy["duration_seconds"] = round(time.time() - start_time, 2)
                elif "completed_at" in status_copy:
                    status_copy["duration_seconds"] = round(status_copy["completed_at"] - start_time, 2)
                elif "failed_at" in status_copy:
                    status_copy["duration_seconds"] = round(status_copy["failed_at"] - start_time, 2)
            
            operations[image_name] = status_copy
        
        return {
            "pull_operations": operations,
            "total_operations": len(operations)
        }

    def cleanup(self):
        """Clean up resources when shutting down"""
        self._stop_cleanup.set()
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=1)
