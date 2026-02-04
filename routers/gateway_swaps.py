"""
Gateway Swaps Router - Swap history and status endpoints.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import AsyncDatabaseManager
from database.repositories import GatewaySwapRepository
from deps import get_database_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway Swaps"], prefix="/gateway")


@router.get("/swaps/{transaction_hash}/status")
async def get_swap_status(
    transaction_hash: str,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
):
    """
    Get status of a specific swap by transaction hash.
    """
    try:
        async with db_manager.get_session_context() as session:
            swap_repo = GatewaySwapRepository(session)
            swap = await swap_repo.get_swap_by_tx_hash(transaction_hash)

            if not swap:
                raise HTTPException(status_code=404, detail=f"Swap not found: {transaction_hash}")

            return swap_repo.to_dict(swap)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error getting swap status: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting swap status: {exc}")


@router.post("/swaps/search")
async def search_swaps(
    network: Optional[str] = Query(default=None),
    connector: Optional[str] = Query(default=None),
    wallet_address: Optional[str] = Query(default=None),
    trading_pair: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
):
    """
    Search swap history with filters.
    """
    try:
        async with db_manager.get_session_context() as session:
            swap_repo = GatewaySwapRepository(session)
            swaps = await swap_repo.get_swaps(
                network=network,
                connector=connector,
                wallet_address=wallet_address,
                trading_pair=trading_pair,
                status=status,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                offset=offset,
            )

            has_more = len(swaps) == limit
            return {
                "data": [swap_repo.to_dict(swap) for swap in swaps],
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "has_more": has_more,
                    "total_count": len(swaps) + offset if not has_more else None,
                },
            }
    except Exception as exc:
        logger.error(f"Error searching swaps: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error searching swaps: {exc}")


@router.get("/swaps/summary")
async def get_swaps_summary(
    network: Optional[str] = Query(default=None),
    wallet_address: Optional[str] = Query(default=None),
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
):
    """
    Get swap summary statistics.
    """
    try:
        async with db_manager.get_session_context() as session:
            swap_repo = GatewaySwapRepository(session)
            summary = await swap_repo.get_swaps_summary(
                network=network,
                wallet_address=wallet_address,
                start_time=start_time,
                end_time=end_time,
            )
            return summary
    except Exception as exc:
        logger.error(f"Error getting swaps summary: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting swaps summary: {exc}")
