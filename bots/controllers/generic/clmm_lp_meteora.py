from decimal import Decimal
from typing import Optional

from pydantic import Field, field_validator

from . import clmm_lp_base
from .clmm_lp_domain.components import PoolDomainAdapter
from .clmm_lp_domain.policies import MeteoraPolicy


class CLMMLPMeteoraConfig(clmm_lp_base.CLMMLPBaseConfig):
    controller_name: str = "clmm_lp_meteora"
    connector_name: str = "meteora/clmm"
    router_connector: str = "jupiter/router"
    trading_pair: str = "SOL-USDC"

    ratio_edge_buffer_pct: Decimal = Field(default=Decimal("0.02"), json_schema_extra={"is_updatable": True})
    strategy_type: Optional[int] = Field(default=None, json_schema_extra={"is_updatable": True})

    @field_validator("ratio_edge_buffer_pct", mode="after")
    @classmethod
    def validate_ratio_edge_buffer_pct(cls, v):
        if v is None or v < 0:
            raise ValueError("ratio_edge_buffer_pct must be >= 0")
        if v >= 1:
            raise ValueError("ratio_edge_buffer_pct must be < 1")
        return v

    @field_validator("strategy_type", mode="after")
    @classmethod
    def validate_strategy_type(cls, v):
        if v is None:
            return v
        if v not in (0, 1, 2):
            raise ValueError("strategy_type must be 0, 1, or 2")
        return v


class CLMMLPMeteoraController(clmm_lp_base.CLMMLPBaseController):
    def __init__(self, config: CLMMLPMeteoraConfig, *args, **kwargs):
        domain = PoolDomainAdapter.from_config(config.trading_pair, config.pool_trading_pair)
        super().__init__(config, MeteoraPolicy(config, domain), domain, *args, **kwargs)
