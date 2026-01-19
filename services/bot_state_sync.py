"""Bot State Synchronization Service.

This service ensures that the database state (BotRun) accurately reflects
the actual state of bots detected via MQTT and Docker.

Key responsibilities:
1. CREATED -> RUNNING: When a bot starts sending performance data
2. CREATED -> STOPPED: When a bot's container no longer exists (orphan detection)
3. RUNNING -> STOPPED: When a running bot's container disappears

Design rationale (from official hummingbot-dashboard analysis):
- MQTT + Docker provide the REAL-TIME truth about bot state
- Database (BotRun) is for HISTORICAL tracking and auditing
- This service bridges the gap by syncing real-time state to database
"""
import asyncio
import logging
from typing import Set, Optional

from database import AsyncDatabaseManager, BotRunRepository
from services.bots_orchestrator import BotsOrchestrator

logger = logging.getLogger(__name__)


class BotStateSyncService:
    """Service to synchronize bot states between real-time sources and database."""

    def __init__(
        self,
        bots_orchestrator: BotsOrchestrator,
        db_manager: AsyncDatabaseManager,
        sync_interval: float = 10.0,  # Sync every 10 seconds
    ):
        self.bots_orchestrator = bots_orchestrator
        self.db_manager = db_manager
        self.sync_interval = sync_interval
        self._sync_task: Optional[asyncio.Task] = None
        self._running = False

        # Track which bots have been marked as RUNNING to avoid duplicate DB calls
        self._confirmed_running: Set[str] = set()

    def start(self):
        """Start the sync service."""
        if self._running:
            return
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("BotStateSyncService started")

    def stop(self):
        """Stop the sync service."""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            self._sync_task = None
        logger.info("BotStateSyncService stopped")

    async def _sync_loop(self):
        """Main sync loop that runs periodically."""
        while self._running:
            try:
                await self._sync_states()
            except Exception as e:
                logger.error(f"Error in bot state sync: {e}", exc_info=True)

            await asyncio.sleep(self.sync_interval)

    async def _sync_states(self):
        """Synchronize bot states between real-time sources and database.

        State transitions handled:
        1. CREATED + has_performance -> RUNNING (bot started successfully)
        2. CREATED + no_container + no_mqtt -> STOPPED (orphan/crash)
        3. RUNNING + no_container + no_mqtt -> STOPPED (unexpected stop)
        """
        # Get real-time state from MQTT/Docker
        active_bots = self.bots_orchestrator.active_bots
        mqtt_manager = self.bots_orchestrator.mqtt_manager

        # Get all bots with performance data (indicates they're actually running)
        bots_with_performance = set()
        for bot_name in active_bots:
            reports = mqtt_manager.get_bot_controller_reports(bot_name)
            if reports:
                bots_with_performance.add(bot_name)

        # Get Docker containers
        try:
            docker_bots = await self.bots_orchestrator.get_active_containers()
            docker_bots_set = set(docker_bots)
        except Exception:
            docker_bots_set = set()

        # Sync with database
        async with self.db_manager.get_session_context() as session:
            repo = BotRunRepository(session)

            # 1. Handle CREATED -> RUNNING transitions
            created_runs = await repo.get_created_bot_runs()
            for run in created_runs:
                bot_name = run.bot_name

                # Skip if already confirmed running (avoid duplicate DB calls)
                if bot_name in self._confirmed_running:
                    continue

                # Check if bot has performance data (real indication of running)
                if bot_name in bots_with_performance:
                    await repo.update_bot_run_running(bot_name)
                    self._confirmed_running.add(bot_name)
                    logger.info(f"State transition: {bot_name} CREATED -> RUNNING (has performance data)")

                # Check if container doesn't exist and no MQTT data (orphan)
                elif bot_name not in docker_bots_set and bot_name not in active_bots:
                    await repo.mark_orphan_as_stopped(bot_name)
                    logger.warning(f"State transition: {bot_name} CREATED -> STOPPED (orphan - no container/MQTT)")

            # 2. Handle RUNNING bots that disappeared
            running_runs = await repo.get_active_bot_runs()
            for run in running_runs:
                bot_name = run.bot_name

                # Check if bot is no longer active
                if bot_name not in active_bots and bot_name not in docker_bots_set:
                    # Bot disappeared - mark as stopped
                    await repo.update_bot_run_stopped(
                        bot_name,
                        error_message="Bot disappeared from MQTT and Docker without graceful shutdown"
                    )
                    self._confirmed_running.discard(bot_name)
                    logger.warning(f"State transition: {bot_name} RUNNING -> STOPPED (disappeared)")

            # 3. Clean up confirmed_running set for bots no longer in CREATED state
            # This handles the case where a bot was stopped and redeployed
            for bot_name in list(self._confirmed_running):
                if bot_name not in bots_with_performance:
                    self._confirmed_running.discard(bot_name)

    def clear_confirmed_running(self, bot_name: str):
        """Clear a bot from the confirmed running set.

        Called when a bot is stopped to allow re-tracking if redeployed.
        """
        self._confirmed_running.discard(bot_name)
        logger.debug(f"Cleared {bot_name} from confirmed_running set")
