import asyncio
import logging
import os
import re
import secrets
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks

# Create module-specific logger
logger = logging.getLogger(__name__)

from models import StartBotAction, StopBotAction, V2ScriptDeployment, V2ControllerDeployment
from services.bots_orchestrator import BotsOrchestrator
from services.docker_service import DockerService
from services.bot_state_sync import BotStateSyncService
from deps import (
    get_bots_orchestrator,
    get_docker_service,
    get_bot_archiver,
    get_database_manager,
    get_bot_state_sync,
    get_accounts_service,
)
from utils.file_system import fs_util
from utils.bot_archiver import BotArchiver
from database import AsyncDatabaseManager, BotRunRepository
from services.accounts_service import AccountsService
from services.gateway_client import GatewayClient
from config import settings

router = APIRouter(tags=["Bot Orchestration"], prefix="/bot-orchestration")

_INSTANCE_TIMESTAMP_RE = re.compile(r"\d{8}-\d{4}(\d{2})?")
_INSTANCE_SUFFIX_RE = re.compile(r"-[0-9a-f]{4}$")
_INSTANCE_INVALID_CHARS_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _sanitize_instance_name(instance_name: str) -> str:
    if not instance_name:
        return "bot"
    normalized = instance_name.strip().replace(" ", "-")
    normalized = _INSTANCE_INVALID_CHARS_RE.sub("-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    normalized = normalized.strip("-.")
    normalized = re.sub(r"^[^a-zA-Z0-9]+", "", normalized)
    if not normalized:
        return "bot"
    return normalized


def _build_controller_instance_names(instance_name: str) -> tuple[str, str]:
    normalized_name = _sanitize_instance_name(instance_name)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    has_timestamp = bool(_INSTANCE_TIMESTAMP_RE.search(normalized_name))
    has_suffix = bool(_INSTANCE_SUFFIX_RE.search(normalized_name))

    if has_timestamp:
        unique_instance_name = normalized_name if has_suffix else f"{normalized_name}-{suffix}"
    else:
        unique_instance_name = f"{normalized_name}-{timestamp}-{suffix}"

    return unique_instance_name, f"{unique_instance_name}.yml"


async def _resolve_chain_for_wallet(
    accounts_service: AccountsService,
    wallet_address: str,
) -> Optional[str]:
    wallets = await accounts_service.gateway_client.get_wallets()
    if not wallets:
        return None
    for wallet_group in wallets:
        chain = wallet_group.get("chain")
        addresses = wallet_group.get("walletAddresses", []) or []
        if wallet_address in addresses:
            return chain
    return None


async def _apply_gateway_defaults(
    accounts_service: AccountsService,
    gateway_network_id: Optional[str],
    gateway_wallet_address: Optional[str],
) -> None:
    if not gateway_network_id and not gateway_wallet_address:
        return

    if not await accounts_service.gateway_client.ping():
        raise HTTPException(status_code=503, detail="Gateway service is not available")

    chain = None
    network = None
    if gateway_network_id:
        try:
            chain, network = GatewayClient.parse_network_id(gateway_network_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        result = await accounts_service.gateway_client.update_config(chain, "defaultNetwork", network)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(
                status_code=result.get("status", 500),
                detail=f"Gateway error updating default network: {result.get('error')}",
            )

    if gateway_wallet_address:
        if not chain:
            chain = await _resolve_chain_for_wallet(accounts_service, gateway_wallet_address)
        if not chain:
            raise HTTPException(
                status_code=400,
                detail="Unable to resolve chain for gateway wallet address. Provide gateway_network_id.",
            )

        result = await accounts_service.gateway_client.update_config(
            chain, "defaultWallet", gateway_wallet_address
        )
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(
                status_code=result.get("status", 500),
                detail=f"Gateway error updating default wallet: {result.get('error')}",
            )


@router.get("/status")
def get_active_bots_status(bots_manager: BotsOrchestrator = Depends(get_bots_orchestrator)):
    """
    Get the status of all active bots.
    
    Args:
        bots_manager: Bot orchestrator service dependency
        
    Returns:
        Dictionary with status and data containing all active bot statuses
    """
    return {"status": "success", "data": bots_manager.get_all_bots_status()}


@router.get("/mqtt")
def get_mqtt_status(bots_manager: BotsOrchestrator = Depends(get_bots_orchestrator)):
    """
    Get MQTT connection status and discovered bots.
    
    Args:
        bots_manager: Bot orchestrator service dependency
        
    Returns:
        Dictionary with MQTT connection status, discovered bots, and broker information
    """
    mqtt_connected = bots_manager.mqtt_manager.is_connected
    discovered_bots = bots_manager.mqtt_manager.get_discovered_bots(
        timeout_seconds=settings.app.mqtt_activity_timeout_seconds
    )
    active_bots = list(bots_manager.active_bots.keys())
    
    # Check client state
    client_state = "connected" if bots_manager.mqtt_manager.is_connected else "disconnected"
    
    return {
        "status": "success",
        "data": {
            "mqtt_connected": mqtt_connected,
            "discovered_bots": discovered_bots,
            "active_bots": active_bots,
            "broker_host": bots_manager.broker_host,
            "broker_port": bots_manager.broker_port,
            "broker_username": bots_manager.broker_username,
            "client_state": client_state
        }
    }


@router.get("/instances")
def get_instances_summary(
    bots_manager: BotsOrchestrator = Depends(get_bots_orchestrator),
    docker_service: DockerService = Depends(get_docker_service),
):
    """Return a unified instance view that merges Docker and MQTT states."""
    infrastructure = {
        "hummingbot-api",
        "hummingbot-broker",
        "hummingbot-postgres",
        "gateway",
        "lp-dashboard",
        "dashboard",
    }

    running = docker_service.get_active_containers()
    exited = docker_service.get_exited_containers()

    if isinstance(running, str) or isinstance(exited, str):
        detail = running if isinstance(running, str) else exited
        raise HTTPException(status_code=500, detail=f"Failed to read Docker containers: {detail}")

    containers = {}

    def add_container(container_info):
        name = container_info.get("name")
        if not name or name in infrastructure:
            return
        containers[name] = {
            "name": name,
            "docker_status": container_info.get("status", "unknown"),
            "image": container_info.get("image", "unknown"),
        }

    for container in exited:
        add_container(container)
    for container in running:
        add_container(container)

    timeout_seconds = settings.app.mqtt_activity_timeout_seconds
    last_seen_map = bots_manager.mqtt_manager.get_last_seen_map()
    discovered = set(bots_manager.mqtt_manager.get_discovered_bots(timeout_seconds=timeout_seconds))
    active_bots = set(bots_manager.active_bots.keys())
    now = time.time()

    for name, info in containers.items():
        if name in discovered:
            mqtt_status = "connected"
        elif name in active_bots:
            mqtt_status = "stale"
        else:
            mqtt_status = "disconnected"

        docker_status = info.get("docker_status")

        if docker_status == "running" and mqtt_status == "connected":
            health_state = "running"
            reason = None
        elif docker_status == "running" and mqtt_status == "stale":
            health_state = "degraded"
            reason = "mqtt_stale"
        elif docker_status == "running" and mqtt_status == "disconnected":
            health_state = "degraded"
            reason = "mqtt_disconnected"
        elif docker_status in {"exited", "created", "dead"}:
            health_state = "stopped"
            reason = "container_stopped"
        else:
            health_state = "unknown"
            reason = None

        last_seen = last_seen_map.get(name)
        last_seen_age = now - last_seen if last_seen else None

        info.update(
            mqtt_status=mqtt_status,
            recently_active=name in discovered,
            mqtt_last_seen=last_seen,
            mqtt_last_seen_age=last_seen_age,
            health_state=health_state,
            reason=reason,
        )

    for name in discovered:
        if name not in containers:
            last_seen = last_seen_map.get(name)
            last_seen_age = now - last_seen if last_seen else None
            containers[name] = {
                "name": name,
                "docker_status": "missing",
                "image": "unknown",
                "mqtt_status": "connected",
                "recently_active": True,
                "mqtt_last_seen": last_seen,
                "mqtt_last_seen_age": last_seen_age,
                "health_state": "orphaned",
                "reason": "container_missing",
            }

    instances = sorted(containers.values(), key=lambda item: item["name"])
    return {"status": "success", "data": {"instances": instances}}


@router.get("/{bot_name}/status")
def get_bot_status(bot_name: str, bots_manager: BotsOrchestrator = Depends(get_bots_orchestrator)):
    """
    Get the status of a specific bot.
    
    Args:
        bot_name: Name of the bot to get status for
        bots_manager: Bot orchestrator service dependency
        
    Returns:
        Dictionary with bot status information
        
    Raises:
        HTTPException: 404 if bot not found
    """
    response = bots_manager.get_bot_status(bot_name)
    if not response:
        raise HTTPException(status_code=404, detail="Bot not found")
    return {
        "status": "success",
        "data": response
    }


@router.get("/{bot_name}/history")
async def get_bot_history(
    bot_name: str, 
    days: int = 0, 
    verbose: bool = False, 
    precision: int = None, 
    timeout: float = 30.0,
    bots_manager: BotsOrchestrator = Depends(get_bots_orchestrator)
):
    """
    Get trading history for a bot with optional parameters.
    
    Args:
        bot_name: Name of the bot to get history for
        days: Number of days of history to retrieve (0 for all)
        verbose: Whether to include verbose output
        precision: Decimal precision for numerical values
        timeout: Timeout in seconds for the operation
        bots_manager: Bot orchestrator service dependency
        
    Returns:
        Dictionary with bot trading history
    """
    response = await bots_manager.get_bot_history(
        bot_name, 
        days=days, 
        verbose=verbose, 
        precision=precision, 
        timeout=timeout
    )
    return {"status": "success", "response": response}


@router.post("/start-bot")
async def start_bot(
    action: StartBotAction, 
    bots_manager: BotsOrchestrator = Depends(get_bots_orchestrator),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Start a bot with the specified configuration.
    
    Args:
        action: StartBotAction containing bot configuration parameters
        bots_manager: Bot orchestrator service dependency
        db_manager: Database manager dependency
        
    Returns:
        Dictionary with status and response from bot start operation
    """
    response = await bots_manager.start_bot(action.bot_name, log_level=action.log_level, script=action.script,
                                      conf=action.conf, async_backend=action.async_backend)
    
    # Bot run tracking simplified - only track deployment and stop times
    
    return {"status": "success", "response": response}


@router.post("/stop-bot")
async def stop_bot(
    action: StopBotAction,
    bots_manager: BotsOrchestrator = Depends(get_bots_orchestrator),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
    bot_state_sync: BotStateSyncService = Depends(get_bot_state_sync)
):
    """
    Stop a bot with the specified configuration.
    
    Args:
        action: StopBotAction containing bot stop parameters
        bots_manager: Bot orchestrator service dependency
        db_manager: Database manager dependency
        
    Returns:
        Dictionary with status and response from bot stop operation
    """
    # Capture final status BEFORE stopping (performance data is cleared on stop)
    final_status = None
    try:
        final_status = bots_manager.get_bot_status(action.bot_name)
        logger.info(f"Captured final status for {action.bot_name} before stopping")
    except Exception as e:
        logger.warning(f"Failed to capture final status for {action.bot_name}: {e}")

    response = await bots_manager.stop_bot(action.bot_name, skip_order_cancellation=action.skip_order_cancellation,
                                     async_backend=action.async_backend)

    # Update bot run status to STOPPED if stop was successful
    if response.get("success"):
        try:
            async with db_manager.get_session_context() as session:
                bot_run_repo = BotRunRepository(session)
                await bot_run_repo.update_bot_run_stopped(
                    action.bot_name,
                    final_status=final_status
                )
                logger.info(f"Updated bot run status to STOPPED for {action.bot_name}")

            # Clear confirmed_running state to allow re-tracking if redeployed
            bot_state_sync.clear_confirmed_running(action.bot_name)
            logger.info(f"Cleared confirmed_running state for {action.bot_name}")
        except Exception as e:
            logger.error(f"Failed to update bot run status: {e}")
            # Don't fail the stop operation if bot run update fails

    return {"status": "success", "response": response}


@router.get("/bot-runs")
async def get_bot_runs(
    bot_name: str = None,
    account_name: str = None,
    strategy_type: str = None,
    strategy_name: str = None,
    run_status: str = None,
    deployment_status: str = None,
    limit: int = 100,
    offset: int = 0,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Get bot runs with optional filtering.
    
    Args:
        bot_name: Filter by bot name
        account_name: Filter by account name
        strategy_type: Filter by strategy type (script or controller)
        strategy_name: Filter by strategy name
        run_status: Filter by run status (CREATED, RUNNING, STOPPED, ERROR)
        deployment_status: Filter by deployment status (DEPLOYED, FAILED, ARCHIVED)
        limit: Maximum number of results to return
        offset: Number of results to skip
        db_manager: Database manager dependency
        
    Returns:
        List of bot runs with their details
    """
    try:
        async with db_manager.get_session_context() as session:
            bot_run_repo = BotRunRepository(session)
            bot_runs = await bot_run_repo.get_bot_runs(
                bot_name=bot_name,
                account_name=account_name,
                strategy_type=strategy_type,
                strategy_name=strategy_name,
                run_status=run_status,
                deployment_status=deployment_status,
                limit=limit,
                offset=offset
            )
            
            # Convert bot runs to dictionaries for JSON serialization
            runs_data = []
            for run in bot_runs:
                run_dict = {
                    "id": run.id,
                    "bot_name": run.bot_name,
                    "instance_name": run.instance_name,
                    "deployed_at": run.deployed_at.isoformat() if run.deployed_at else None,
                    "stopped_at": run.stopped_at.isoformat() if run.stopped_at else None,
                    "strategy_type": run.strategy_type,
                    "strategy_name": run.strategy_name,
                    "config_name": run.config_name,
                    "account_name": run.account_name,
                    "image_version": run.image_version,
                    "deployment_status": run.deployment_status,
                    "run_status": run.run_status,
                    "deployment_config": run.deployment_config,
                    "final_status": run.final_status,
                    "error_message": run.error_message
                }
                runs_data.append(run_dict)
            
            return {
                "status": "success", 
                "data": runs_data,
                "total": len(runs_data),
                "limit": limit,
                "offset": offset
            }
    except Exception as e:
        logger.error(f"Failed to get bot runs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/bot-runs/by-name/{bot_name}")
async def delete_bot_runs_by_name(
    bot_name: str,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Delete all bot run records for a specific bot name.

    Args:
        bot_name: Name of the bot to delete runs for
        db_manager: Database manager dependency

    Returns:
        Success message with count of deleted records
    """
    try:
        async with db_manager.get_session_context() as session:
            bot_run_repo = BotRunRepository(session)
            count = await bot_run_repo.delete_bot_run_by_name(bot_name)

            return {"status": "success", "message": f"Deleted {count} bot run(s) for {bot_name}", "deleted_count": count}
    except Exception as e:
        logger.error(f"Failed to delete bot runs for {bot_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bot-runs/{bot_run_id}")
async def get_bot_run_by_id(
    bot_run_id: int,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Get a specific bot run by ID.

    Args:
        bot_run_id: ID of the bot run
        db_manager: Database manager dependency

    Returns:
        Bot run details

    Raises:
        HTTPException: 404 if bot run not found
    """
    try:
        async with db_manager.get_session_context() as session:
            bot_run_repo = BotRunRepository(session)
            bot_run = await bot_run_repo.get_bot_run_by_id(bot_run_id)
            
            if not bot_run:
                raise HTTPException(status_code=404, detail=f"Bot run {bot_run_id} not found")
            
            run_dict = {
                "id": bot_run.id,
                "bot_name": bot_run.bot_name,
                "instance_name": bot_run.instance_name,
                "deployed_at": bot_run.deployed_at.isoformat() if bot_run.deployed_at else None,
                "stopped_at": bot_run.stopped_at.isoformat() if bot_run.stopped_at else None,
                "strategy_type": bot_run.strategy_type,
                "strategy_name": bot_run.strategy_name,
                "config_name": bot_run.config_name,
                "account_name": bot_run.account_name,
                "image_version": bot_run.image_version,
                "deployment_status": bot_run.deployment_status,
                "run_status": bot_run.run_status,
                "deployment_config": bot_run.deployment_config,
                "final_status": bot_run.final_status,
                "error_message": bot_run.error_message
            }
            
            return {"status": "success", "data": run_dict}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get bot run {bot_run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bot-runs/stats")
async def get_bot_run_stats(
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Get statistics about bot runs.

    Args:
        db_manager: Database manager dependency

    Returns:
        Bot run statistics
    """
    try:
        async with db_manager.get_session_context() as session:
            bot_run_repo = BotRunRepository(session)
            stats = await bot_run_repo.get_bot_run_stats()

            return {"status": "success", "data": stats}
    except Exception as e:
        logger.error(f"Failed to get bot run stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/bot-runs/{bot_run_id}")
async def delete_bot_run(
    bot_run_id: int,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Delete a bot run record by ID.

    Args:
        bot_run_id: ID of the bot run to delete
        db_manager: Database manager dependency

    Returns:
        Success message if deleted

    Raises:
        HTTPException: 404 if bot run not found
    """
    try:
        async with db_manager.get_session_context() as session:
            bot_run_repo = BotRunRepository(session)
            deleted = await bot_run_repo.delete_bot_run(bot_run_id)

            if not deleted:
                raise HTTPException(status_code=404, detail=f"Bot run {bot_run_id} not found")

            return {"status": "success", "message": f"Bot run {bot_run_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete bot run {bot_run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _background_stop_and_archive(
    bot_name: str,
    container_name: str,
    bot_name_for_orchestrator: str,
    skip_order_cancellation: bool,
    archive_locally: bool,
    s3_bucket: str,
    bots_manager: BotsOrchestrator,
    docker_manager: DockerService,
    bot_archiver: BotArchiver,
    db_manager: AsyncDatabaseManager,
    bot_state_sync: BotStateSyncService
):
    """Background task to handle the stop and archive process"""
    try:
        logger.info(f"Starting background stop-and-archive for {bot_name}")
        
        # Step 1: Capture bot final status before stopping (while bot is still running)
        logger.info(f"Capturing final status for {bot_name_for_orchestrator}")
        final_status = None
        try:
            final_status = bots_manager.get_bot_status(bot_name_for_orchestrator)
            logger.info(f"Captured final status for {bot_name_for_orchestrator}: {final_status}")
        except Exception as e:
            logger.warning(f"Failed to capture final status for {bot_name_for_orchestrator}: {e}")
        
        # Step 2: Update bot run with stopped_at timestamp and final status before stopping
        try:
            async with db_manager.get_session_context() as session:
                bot_run_repo = BotRunRepository(session)
                await bot_run_repo.update_bot_run_stopped(
                    bot_name,
                    final_status=final_status
                )
                logger.info(f"Updated bot run with stopped_at timestamp and final status for {bot_name}")
        except Exception as e:
            logger.error(f"Failed to update bot run with stopped status: {e}")
            # Continue with stop process even if database update fails
        
        # Step 3: Mark the bot as stopping, and stop the bot trading process
        bots_manager.set_bot_stopping(bot_name_for_orchestrator)
        logger.info(f"Stopping bot trading process for {bot_name_for_orchestrator}")
        stop_response = await bots_manager.stop_bot(
            bot_name_for_orchestrator, 
            skip_order_cancellation=skip_order_cancellation,
            async_backend=True  # Always use async for background tasks
        )
        
        if not stop_response or not stop_response.get("success", False):
            error_msg = stop_response.get('error', 'Unknown error') if stop_response else 'No response from bot orchestrator'
            logger.error(f"Failed to stop bot process: {error_msg}")
            return
        
        # Step 4: Wait for graceful shutdown (15 seconds as requested)
        logger.info(f"Waiting 15 seconds for bot {bot_name} to gracefully shutdown")
        await asyncio.sleep(15)
        
        # Step 5: Stop the container with monitoring
        max_retries = 10
        retry_interval = 2
        container_stopped = False
        
        for i in range(max_retries):
            logger.info(f"Attempting to stop container {container_name} (attempt {i+1}/{max_retries})")
            docker_manager.stop_container(container_name)
                
            # Check if container is already stopped
            container_status = docker_manager.get_container_status(container_name)
            if container_status.get("state", {}).get("status") == "exited":
                container_stopped = True
                logger.info(f"Container {container_name} is already stopped")
                break
                
            await asyncio.sleep(retry_interval)
        
        if not container_stopped:
            logger.error(f"Failed to stop container {container_name} after {max_retries} attempts")
            return
        
        # Step 6: Archive the bot data
        instance_dir = os.path.join('bots', 'instances', container_name)
        logger.info(f"Archiving bot data from {instance_dir}")
        
        try:
            if archive_locally:
                bot_archiver.archive_locally(container_name, instance_dir)
            else:
                bot_archiver.archive_and_upload(container_name, instance_dir, bucket_name=s3_bucket)
            logger.info(f"Successfully archived bot data for {container_name}")
        except Exception as e:
            logger.error(f"Archive failed: {str(e)}")
            # Continue with removal even if archive fails
            
        # Step 7: Remove the container
        logging.info(f"Removing container {container_name}")
        remove_response = docker_manager.remove_container(container_name, force=False)
        
        if not remove_response.get("success"):
            # If graceful remove fails, try force remove
            logging.warning("Graceful container removal failed, attempting force removal")
            remove_response = docker_manager.remove_container(container_name, force=True)
        
        if remove_response.get("success"):
            logging.info(f"Successfully completed stop-and-archive for bot {bot_name}")
            
            # Step 8: Update bot run deployment status to ARCHIVED
            try:
                async with db_manager.get_session_context() as session:
                    bot_run_repo = BotRunRepository(session)
                    await bot_run_repo.update_bot_run_archived(bot_name)
                    logger.info(f"Updated bot run deployment status to ARCHIVED for {bot_name}")
            except Exception as e:
                logger.error(f"Failed to update bot run to archived: {e}")
        else:
            logging.error(f"Failed to remove container {container_name}")
            
            # Update bot run with error status (but keep stopped_at timestamp from earlier)
            try:
                async with db_manager.get_session_context() as session:
                    bot_run_repo = BotRunRepository(session)
                    await bot_run_repo.update_bot_run_stopped(
                        bot_name,
                        error_message="Failed to remove container during archive process"
                    )
                    logger.info(f"Updated bot run with error status for {bot_name}")
            except Exception as e:
                logger.error(f"Failed to update bot run with error: {e}")
            
    except Exception as e:
        logging.error(f"Error in background stop-and-archive for {bot_name}: {str(e)}")
        
        # Update bot run with error status
        try:
            async with db_manager.get_session_context() as session:
                bot_run_repo = BotRunRepository(session)
                await bot_run_repo.update_bot_run_stopped(
                    bot_name,
                    error_message=str(e)
                )
                logger.info(f"Updated bot run with error status for {bot_name}")
        except Exception as db_error:
            logger.error(f"Failed to update bot run with error: {db_error}")
    finally:
        # Always clear the stopping status when the background task completes
        bots_manager.clear_bot_stopping(bot_name_for_orchestrator)
        logger.info(f"Cleared stopping status for bot {bot_name}")

        # Clear confirmed_running state to allow re-tracking if redeployed
        bot_state_sync.clear_confirmed_running(bot_name)
        bot_state_sync.clear_confirmed_running(bot_name_for_orchestrator)
        logger.info(f"Cleared confirmed_running state for bot {bot_name}")

        # Remove bot from active_bots and clear all MQTT data
        if bot_name_for_orchestrator in bots_manager.active_bots:
            bots_manager.mqtt_manager.clear_bot_data(bot_name_for_orchestrator)
            del bots_manager.active_bots[bot_name_for_orchestrator]
            logger.info(f"Removed bot {bot_name_for_orchestrator} from active_bots and cleared MQTT data")


@router.post("/stop-and-archive-bot/{bot_name}")
async def stop_and_archive_bot(
    bot_name: str,
    background_tasks: BackgroundTasks,
    skip_order_cancellation: bool = True,
    archive_locally: bool = True,
    s3_bucket: str = None,
    bots_manager: BotsOrchestrator = Depends(get_bots_orchestrator),
    docker_manager: DockerService = Depends(get_docker_service),
    bot_archiver: BotArchiver = Depends(get_bot_archiver),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
    bot_state_sync: BotStateSyncService = Depends(get_bot_state_sync)
):
    """
    Gracefully stop a bot and archive its data in the background.
    This initiates a background task that will:
    1. Stop the bot trading process via MQTT
    2. Wait 15 seconds for graceful shutdown
    3. Monitor and stop the Docker container
    4. Archive the bot data (locally or to S3)
    5. Remove the container
    
    Returns immediately with a success message while the process continues in the background.
    """
    try:
        # Step 1: Normalize bot name and container name
        # Container name is now the same as bot name (no prefix added)
        actual_bot_name = bot_name
        container_name = bot_name
        
        logging.info(f"Normalized bot_name: {actual_bot_name}, container_name: {container_name}")
        
        # Step 2: Validate bot exists in active bots
        active_bots = list(bots_manager.active_bots.keys())
        
        # Check if bot exists in active bots (could be stored as either format)
        bot_found = (actual_bot_name in active_bots) or (container_name in active_bots)
        
        if not bot_found:
            return {
                "status": "error",
                "message": f"Bot '{actual_bot_name}' not found in active bots. Active bots: {active_bots}. Cannot perform graceful shutdown.",
                "details": {
                    "input_name": bot_name,
                    "actual_bot_name": actual_bot_name,
                    "container_name": container_name,
                    "active_bots": active_bots,
                    "reason": "Bot must be actively managed via MQTT for graceful shutdown"
                }
            }
        
        # Use the format that's actually stored in active bots
        bot_name_for_orchestrator = container_name if container_name in active_bots else actual_bot_name

        # Add the background task
        background_tasks.add_task(
            _background_stop_and_archive,
            bot_name=actual_bot_name,
            container_name=container_name,
            bot_name_for_orchestrator=bot_name_for_orchestrator,
            skip_order_cancellation=skip_order_cancellation,
            archive_locally=archive_locally,
            s3_bucket=s3_bucket,
            bots_manager=bots_manager,
            docker_manager=docker_manager,
            bot_archiver=bot_archiver,
            db_manager=db_manager,
            bot_state_sync=bot_state_sync
        )
        
        return {
            "status": "success",
            "message": f"Stop and archive process started for bot {actual_bot_name}",
            "details": {
                "input_name": bot_name,
                "actual_bot_name": actual_bot_name,
                "container_name": container_name,
                "process": "The bot will be gracefully stopped, archived, and removed in the background. This process typically takes 20-30 seconds."
            }
        }
        
    except Exception as e:
        logging.error(f"Error initiating stop_and_archive_bot for {bot_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/deploy-v2-script")
async def deploy_v2_script(
    config: V2ScriptDeployment, 
    docker_manager: DockerService = Depends(get_docker_service),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Creates and autostart a v2 script with a configuration if present.
    
    Args:
        config: Configuration for the new Hummingbot instance
        docker_manager: Docker service dependency
        db_manager: Database manager dependency
        
    Returns:
        Dictionary with creation response and instance details
    """
    if config.gateway_network_id or config.gateway_wallet_address:
        await _apply_gateway_defaults(
            accounts_service,
            config.gateway_network_id,
            config.gateway_wallet_address,
        )

    original_instance_name = config.instance_name
    normalized_instance_name = _sanitize_instance_name(config.instance_name)
    config.instance_name = normalized_instance_name

    logging.info(f"Creating hummingbot instance with config: {config}")
    response = docker_manager.create_hummingbot_instance(config)
    if not response.get("success"):
        error_message = response.get("message", "Failed to create instance.")
        raise HTTPException(status_code=500, detail=error_message)
    response["instance_name"] = normalized_instance_name
    if original_instance_name != normalized_instance_name:
        response["normalized_from"] = original_instance_name
    
    # Track bot run if deployment was successful
    if response.get("success"):
        try:
            async with db_manager.get_session_context() as session:
                bot_run_repo = BotRunRepository(session)
                await bot_run_repo.create_bot_run(
                    bot_name=config.instance_name,
                    instance_name=config.instance_name,
                    strategy_type="script",
                    strategy_name=config.script or "unknown",
                    account_name=config.credentials_profile,
                    config_name=config.script_config,
                    image_version=config.image,
                    deployment_config=config.dict()
                )
                logger.info(f"Created bot run record for {config.instance_name}")
        except Exception as e:
            logger.error(f"Failed to create bot run record: {e}")
            # Don't fail the deployment if bot run creation fails
    
    return response


@router.post("/deploy-v2-controllers")
async def deploy_v2_controllers(
    deployment: V2ControllerDeployment,
    docker_manager: DockerService = Depends(get_docker_service),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Deploy a V2 strategy with controllers by generating the script config and creating the instance.
    This endpoint simplifies the deployment process for V2 controller strategies.
    
    Args:
        deployment: V2ControllerDeployment configuration
        docker_manager: Docker service dependency
        
    Returns:
        Dictionary with deployment response and generated configuration details
        
    Raises:
        HTTPException: 500 if deployment fails
    """
    try:
        if deployment.gateway_network_id or deployment.gateway_wallet_address:
            await _apply_gateway_defaults(
                accounts_service,
                deployment.gateway_network_id,
                deployment.gateway_wallet_address,
            )

        original_instance_name = deployment.instance_name
        unique_instance_name, script_config_filename = _build_controller_instance_names(deployment.instance_name)

        # Ensure controller config names have .yml extension
        controllers_with_extension = []
        for controller in deployment.controllers_config:
            if not controller.endswith('.yml'):
                controllers_with_extension.append(f"{controller}.yml")
            else:
                controllers_with_extension.append(controller)

        # Create the script config content
        script_config_content = {
            "script_file_name": "v2_with_controllers.py",
            "candles_config": [],
            "markets": {},
            "controllers_config": controllers_with_extension,
        }

        # Add optional drawdown parameters if provided
        if deployment.max_global_drawdown_quote is not None:
            script_config_content["max_global_drawdown_quote"] = deployment.max_global_drawdown_quote
        if deployment.max_controller_drawdown_quote is not None:
            script_config_content["max_controller_drawdown_quote"] = deployment.max_controller_drawdown_quote

        # Save the script config to the scripts directory
        scripts_dir = os.path.join("conf", "scripts")

        script_config_path = os.path.join(scripts_dir, script_config_filename)
        fs_util.dump_dict_to_yaml(script_config_path, script_config_content)

        logging.info(f"Generated script config: {script_config_filename} with content: {script_config_content}")

        # Create the V2ScriptDeployment with the generated script config
        instance_config = V2ScriptDeployment(
            instance_name=unique_instance_name,
            credentials_profile=deployment.credentials_profile,
            image=deployment.image,
            script="v2_with_controllers.py",
            script_config=script_config_filename,
            gateway_network_id=deployment.gateway_network_id,
            gateway_wallet_address=deployment.gateway_wallet_address,
        )
        
        # Deploy the instance using the existing method
        response = docker_manager.create_hummingbot_instance(instance_config)
        if not response.get("success"):
            error_message = response.get("message", "Failed to create instance.")
            raise HTTPException(status_code=500, detail=error_message)
        
        if response.get("success"):
            response["script_config_generated"] = script_config_filename
            response["controllers_deployed"] = deployment.controllers_config
            response["unique_instance_name"] = unique_instance_name
            if original_instance_name != unique_instance_name:
                response["normalized_from"] = original_instance_name

            # Track bot run if deployment was successful
            try:
                async with db_manager.get_session_context() as session:
                    bot_run_repo = BotRunRepository(session)
                    await bot_run_repo.create_bot_run(
                        bot_name=unique_instance_name,
                        instance_name=unique_instance_name,
                        strategy_type="controller",
                        strategy_name="v2_with_controllers",
                        account_name=deployment.credentials_profile,
                        config_name=script_config_filename,
                        image_version=deployment.image,
                        deployment_config=deployment.dict()
                    )
                    logger.info(f"Created bot run record for controller deployment {unique_instance_name}")
            except Exception as e:
                logger.error(f"Failed to create bot run record: {e}")
                # Don't fail the deployment if bot run creation fails

        return response
        
    except Exception as e:
        logging.error(f"Error deploying V2 controllers: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
