import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch


def _make_test_stubs() -> dict:
    """
    These tests run in a minimal Python environment (no FastAPI / eth_account / etc.).
    To unit-test router logic, we stub the few modules imported by `routers/gateway_clmm.py`.
    """

    # routers/gateway_clmm.py imports aiohttp for external HTTP calls (Meteora/Raydium).
    aiohttp_stub = types.SimpleNamespace(ClientSession=object, ClientError=Exception, ClientResponse=object)

    # Minimal FastAPI surface so the module can be imported and HTTPException can be raised.
    fastapi_stub = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail=None):
            self.status_code = status_code
            self.detail = detail
            super().__init__(str(detail))

    class APIRouter:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, *args, **kwargs):
            def decorator(fn):
                return fn

            return decorator

        def get(self, *args, **kwargs):
            def decorator(fn):
                return fn

            return decorator

    def Depends(dep=None):
        return dep

    def Query(default=None, *args, **kwargs):
        return default

    fastapi_stub.APIRouter = APIRouter
    fastapi_stub.Depends = Depends
    fastapi_stub.HTTPException = HTTPException
    fastapi_stub.Query = Query
    fastapi_stub.Request = object

    # `routers/gateway_clmm.py` imports these for dependency injection only.
    deps_stub = types.ModuleType("deps")
    deps_stub.get_accounts_service = lambda *_args, **_kwargs: None
    deps_stub.get_database_manager = lambda *_args, **_kwargs: None

    # Avoid importing the real `services` package (it imports eth_account, etc.).
    services_pkg = types.ModuleType("services")
    services_pkg.__path__ = []  # treat as package
    accounts_service_mod = types.ModuleType("services.accounts_service")
    accounts_service_mod.AccountsService = object

    # Avoid importing the real database layer; we only need a repository class placeholder.
    database_pkg = types.ModuleType("database")
    database_pkg.__path__ = []  # treat as package

    class AsyncDatabaseManager:  # pragma: no cover - placeholder for type hints
        pass

    database_pkg.AsyncDatabaseManager = AsyncDatabaseManager

    repos_mod = types.ModuleType("database.repositories")

    class GatewayCLMMRepository:
        def __init__(self, _session):
            pass

        async def get_position_by_address(self, _position_address: str):
            return None

    repos_mod.GatewayCLMMRepository = GatewayCLMMRepository

    # Stub the `models` re-export module imported by routers.
    models_stub = types.ModuleType("models")

    @dataclass
    class CLMMClosePositionRequest:
        connector: str
        network: str
        position_address: str
        wallet_address: str | None = None

    class CLMMCollectFeesResponse:
        def __init__(
            self,
            transaction_hash: str,
            position_address: str,
            base_fee_collected=None,
            quote_fee_collected=None,
            status: str = "submitted",
        ):
            self.transaction_hash = transaction_hash
            self.position_address = position_address
            self.base_fee_collected = base_fee_collected
            self.quote_fee_collected = quote_fee_collected
            self.status = status

    # Provide all names referenced by `from models import (...)` in routers/gateway_clmm.py.
    for name in (
        "CLMMOpenPositionRequest",
        "CLMMOpenPositionResponse",
        "CLMMAddLiquidityRequest",
        "CLMMRemoveLiquidityRequest",
        "CLMMCollectFeesRequest",
        "CLMMPositionsOwnedRequest",
        "CLMMGetPositionInfoRequest",
        "CLMMPositionInfo",
        "CLMMPositionInfoDetails",
        "CLMMPoolInfoResponse",
        "CLMMPoolListItem",
        "CLMMPoolListResponse",
        "TimeBasedMetrics",
    ):
        setattr(models_stub, name, object)

    models_stub.CLMMClosePositionRequest = CLMMClosePositionRequest
    models_stub.CLMMCollectFeesResponse = CLMMCollectFeesResponse
    models_stub.CLMMCollectFeesRequest = object

    return {
        "aiohttp": aiohttp_stub,
        "fastapi": fastapi_stub,
        "deps": deps_stub,
        "services": services_pkg,
        "services.accounts_service": accounts_service_mod,
        "database": database_pkg,
        "database.repositories": repos_mod,
        "models": models_stub,
    }


def _load_gateway_clmm_module(repo_root: Path):
    """Load routers/gateway_clmm.py into an isolated module namespace."""
    module_path = repo_root / "routers" / "gateway_clmm.py"
    spec = importlib.util.spec_from_file_location("gateway_clmm_under_test", module_path)
    assert spec and spec.loader, "Failed to create module spec for routers/gateway_clmm.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubGatewayClient:
    async def ping(self) -> bool:
        return True

    @staticmethod
    def parse_network_id(network_id: str):
        # routers/gateway_clmm.py expects `("ethereum", "base")` for `ethereum-base`.
        return "ethereum", "base"

    async def get_wallet_address_or_default(self, chain: str, wallet_address: str | None = None) -> str:
        return wallet_address or "0xdefault"

    async def clmm_position_info(self, connector: str, chain_network: str, position_address: str):
        # Gateway `/trading/clmm/position-info` shape (camelCase).
        return {
            "address": position_address,
            "poolAddress": "0xpool",
            "baseTokenAddress": "0xbase",
            "quoteTokenAddress": "0xquote",
            "baseTokenAmount": 0,
            "quoteTokenAmount": 0,
            "baseFeeAmount": 1.23,
            "quoteFeeAmount": 4.56,
            "lowerBinId": 0,
            "upperBinId": 0,
            "lowerPrice": 0,
            "upperPrice": 0,
            "price": 123.45,
        }

    async def clmm_positions_owned(self, *args, **kwargs):
        # The bug we're fixing is that close requires DB pool_address and uses positions_owned.
        # Once fixed, close should not need to call this at all.
        raise AssertionError("clmm_positions_owned should not be called when closing without DB state")

    async def clmm_close_position(self, connector: str, network: str, wallet_address: str, position_address: str):
        return {
            "txHash": "0xtx",
            "status": 0,
            "data": {},
        }


class _StubAccountsService:
    def __init__(self):
        self.gateway_client = _StubGatewayClient()


class _DummySessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _StubDbManager:
    def get_session_context(self):
        return _DummySessionContext()


class GatewayClmmCloseWithoutDbTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_position_does_not_require_db_pool_address(self):
        repo_root = Path(__file__).resolve().parents[2]
        stubs = _make_test_stubs()

        with patch.dict(sys.modules, stubs, clear=False):
            gateway_clmm_router = _load_gateway_clmm_module(repo_root)
            request = stubs["models"].CLMMClosePositionRequest(
                connector="uniswap",
                network="ethereum-base",
                position_address="4601565",
                wallet_address="0xwallet",
            )

            response = await gateway_clmm_router.close_clmm_position(
                request,
                accounts_service=_StubAccountsService(),
                db_manager=_StubDbManager(),
            )

        self.assertEqual(response.transaction_hash, "0xtx")
        self.assertEqual(response.position_address, "4601565")

    async def test_close_position_surfaces_gateway_error_response(self):
        class _StubGatewayClientError(_StubGatewayClient):
            async def clmm_close_position(self, connector: str, network: str, wallet_address: str, position_address: str):
                return {"error": "Failed to close position", "status": 500}

        class _StubAccountsServiceError(_StubAccountsService):
            def __init__(self):
                self.gateway_client = _StubGatewayClientError()

        repo_root = Path(__file__).resolve().parents[2]
        stubs = _make_test_stubs()

        with patch.dict(sys.modules, stubs, clear=False):
            gateway_clmm_router = _load_gateway_clmm_module(repo_root)
            request = stubs["models"].CLMMClosePositionRequest(
                connector="uniswap",
                network="ethereum-base",
                position_address="4601565",
                wallet_address="0xwallet",
            )

            with self.assertRaises(stubs["fastapi"].HTTPException) as ctx:
                await gateway_clmm_router.close_clmm_position(
                    request,
                    accounts_service=_StubAccountsServiceError(),
                    db_manager=_StubDbManager(),
                )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.detail, "Failed to close position")


if __name__ == "__main__":
    unittest.main()
