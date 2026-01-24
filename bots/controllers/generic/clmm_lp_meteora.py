from decimal import Decimal
from typing import List, Optional

from pydantic import Field, field_validator

from hummingbot.data_feed.candles_feed.data_types import CandlesConfig

from .clmm_lp_base import CLMMLPBaseConfig, CLMMLPBaseController
from .clmm_lp_domain.policies import MeteoraPolicy


class CLMMLPMeteoraConfig(CLMMLPBaseConfig):
    controller_name: str = "clmm_lp_meteora"
    candles_config: List[CandlesConfig] = []

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


class CLMMLPMeteoraController(CLMMLPBaseController):
    def __init__(self, config: CLMMLPMeteoraConfig, *args, **kwargs):
        super().__init__(config, MeteoraPolicy(config), *args, **kwargs)
