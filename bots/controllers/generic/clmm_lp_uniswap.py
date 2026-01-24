from decimal import Decimal
from pydantic import Field, field_validator

from . import clmm_lp_base
from .clmm_lp_domain.policies import UniswapV3Policy


class CLMMLPUniswapConfig(clmm_lp_base.CLMMLPBaseConfig):
    controller_name: str = "clmm_lp_uniswap"
    connector_name: str = "uniswap/clmm"
    router_connector: str = "uniswap/router"
    trading_pair: str = "ETH-USDC"
    pool_address: str = ""

    ratio_clamp_tick_multiplier: int = Field(default=2, json_schema_extra={"is_updatable": True})

    @field_validator("ratio_clamp_tick_multiplier", mode="after")
    @classmethod
    def validate_ratio_clamp_tick_multiplier(cls, v):
        if v is None or v <= 0:
            raise ValueError("ratio_clamp_tick_multiplier must be > 0")
        return v


class CLMMLPUniswapController(clmm_lp_base.CLMMLPBaseController):
    def __init__(self, config: CLMMLPUniswapConfig, *args, **kwargs):
        super().__init__(config, UniswapV3Policy(config), *args, **kwargs)
