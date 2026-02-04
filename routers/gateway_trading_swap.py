"""
Gateway Trading Swap Router - Unified swap operations via Gateway /trading/swap.
"""
import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from deps import get_accounts_service, get_database_manager
from services.accounts_service import AccountsService
from database import AsyncDatabaseManager
from database.repositories import GatewaySwapRepository
from models import (
    SwapQuoteRequest,
    SwapQuoteResponse,
    SwapExecuteRequest,
    SwapExecuteResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway Trading Swaps"], prefix="/gateway/trading/swap")


def get_transaction_status_from_response(gateway_response: dict) -> str:
    """
    Determine transaction status from Gateway response.

    Gateway returns status field in the response:
    - status: 1 = confirmed
    - status: 0 = pending/submitted
    - status: -1 = failed
    """
    status = gateway_response.get("status")
    if status == 1:
        return "CONFIRMED"
    if status == -1:
        return "FAILED"
    return "SUBMITTED"


@router.get("/quote", response_model=SwapQuoteResponse, response_model_by_alias=True)
async def get_swap_quote(
    request: SwapQuoteRequest = Depends(),
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Get a swap quote via Gateway unified trading/swap.
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.quote_swap(
            chain_network=request.chain_network,
            base_token=request.base_token,
            quote_token=request.quote_token,
            amount=float(request.amount),
            side=request.side,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else None,
            connector=request.connector,
        )

        if not result:
            raise HTTPException(status_code=500, detail="Gateway service is not able to quote swap")
        if result.get("error"):
            raise HTTPException(status_code=result.get("status", 502), detail=result.get("error"))

        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error getting swap quote: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting swap quote: {exc}")


@router.post("/execute", response_model=SwapExecuteResponse, response_model_by_alias=True)
async def execute_swap(
    request: SwapExecuteRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
):
    """
    Execute a swap via Gateway unified trading/swap.
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        chain, _ = accounts_service.gateway_client.parse_network_id(request.chain_network)
        wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
            chain=chain,
            wallet_address=request.wallet_address,
        )

        result = await accounts_service.gateway_client.execute_swap(
            chain_network=request.chain_network,
            wallet_address=wallet_address,
            base_token=request.base_token,
            quote_token=request.quote_token,
            amount=float(request.amount),
            side=request.side,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else None,
            connector=request.connector,
        )

        if not result:
            raise HTTPException(status_code=500, detail="Gateway service is not able to execute swap")
        if result.get("error"):
            raise HTTPException(status_code=result.get("status", 502), detail=result.get("error"))

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")

        data = result.get("data") or {}
        amount_in_raw = data.get("amountIn")
        amount_out_raw = data.get("amountOut")

        input_amount = Decimal(str(amount_in_raw)) if amount_in_raw is not None else request.amount
        output_amount = Decimal(str(amount_out_raw)) if amount_out_raw is not None else Decimal("0")
        price = output_amount / input_amount if input_amount > 0 else Decimal("0")

        tx_status = get_transaction_status_from_response(result)
        connector_name = request.connector or "default"
        trading_pair = f"{request.base_token}-{request.quote_token}"

        try:
            async with db_manager.get_session_context() as session:
                swap_repo = GatewaySwapRepository(session)
                swap_data = {
                    "transaction_hash": transaction_hash,
                    "network": request.chain_network,
                    "connector": connector_name,
                    "wallet_address": wallet_address,
                    "trading_pair": trading_pair,
                    "base_token": request.base_token,
                    "quote_token": request.quote_token,
                    "side": request.side,
                    "input_amount": float(input_amount),
                    "output_amount": float(output_amount),
                    "price": float(price),
                    "slippage_pct": float(request.slippage_pct) if request.slippage_pct is not None else None,
                    "status": tx_status,
                    "pool_address": data.get("poolAddress") or result.get("poolAddress"),
                }
                await swap_repo.create_swap(swap_data)
                logger.info(f"Recorded swap in database: {transaction_hash} (status: {tx_status})")
        except Exception as db_error:
            logger.error(f"Error recording swap in database: {db_error}", exc_info=True)

        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error executing swap: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error executing swap: {exc}")
