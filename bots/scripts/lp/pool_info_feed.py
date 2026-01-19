"""
池信息数据源封装，统一通过 Gateway pool-info 拉取并缓存价格。
"""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Dict, Optional, Set

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.connector.gateway.gateway_lp import CLMMPoolInfo
from hummingbot.data_feed.amm_gateway_data_feed import AmmGatewayDataFeed

LogFunc = Callable[[int, str], None]
PriceSource = str


@dataclass
class PoolInfoFeedSettings:
    """
    池信息源配置。

    Attributes:
        refresh_interval_sec: 刷新间隔秒数（<=0 表示每次都刷新）。
        price_source: 价格来源（pool_info/amm_gateway）。
        price_connector: 使用 AmmGatewayDataFeed 时的连接器名称。
        price_order_amount_in_base: 使用 AmmGatewayDataFeed 时的报价基准数量。
    """

    refresh_interval_sec: float = 1.0
    price_source: PriceSource = "pool_info"
    price_connector: Optional[str] = None
    price_order_amount_in_base: Decimal = Decimal("1")


class PoolInfoFeed:
    """
    池信息拉取与缓存管理器（可选使用 AmmGatewayDataFeed 获取价格）。
    """

    SUPPORTED_SOURCES: Set[PriceSource] = {"pool_info", "amm_gateway"}

    def __init__(
        self,
        connectors: Dict[str, ConnectorBase],
        exchange: str,
        trading_pair: str,
        settings: Optional[PoolInfoFeedSettings] = None,
        log_func: Optional[LogFunc] = None,
    ) -> None:
        """
        初始化 PoolInfoFeed。

        Args:
            connectors: 连接器映射。
            exchange: 连接器名称。
            trading_pair: 交易对。
            settings: 池信息源配置（可选）。
            log_func: 日志函数（可选）。
        """
        self._connectors = connectors
        self._exchange = exchange
        self._trading_pair = trading_pair
        self._settings = settings or PoolInfoFeedSettings()
        self._log = log_func or self._default_log

        self._pool_info: Optional[CLMMPoolInfo] = None
        self._price: Optional[Decimal] = None
        self._last_update_ts: float = 0.0
        self._amm_feed: Optional[AmmGatewayDataFeed] = None
        self._amm_feed_connector: Optional[str] = None
        self._amm_feed_amount: Optional[Decimal] = None
        self._amm_feed_interval: Optional[float] = None

    def _default_log(self, level: int, msg: str) -> None:
        """
        默认日志函数，使用模块 logger 输出。

        Args:
            level: 日志级别。
            msg: 日志内容。
        """
        logging.getLogger(__name__).log(level, msg)

    @property
    def pool_info(self) -> Optional[CLMMPoolInfo]:
        """
        获取最近一次拉取的池信息。

        Returns:
            池信息；未拉取时为 None。
        """
        return self._pool_info

    @property
    def price(self) -> Optional[Decimal]:
        """
        获取最近一次拉取的价格。

        Returns:
            价格；未拉取时为 None。
        """
        return self._price

    @property
    def last_update_ts(self) -> float:
        """
        获取最近一次刷新时间戳。

        Returns:
            时间戳。
        """
        return self._last_update_ts

    def start(self) -> None:
        """
        启动价格数据源（如启用 AmmGatewayDataFeed）。
        """
        self._ensure_amm_feed_started()

    def stop(self) -> None:
        """
        停止价格数据源（如启用 AmmGatewayDataFeed）。
        """
        self._stop_amm_feed()

    async def refresh(self, force: bool = False) -> Optional[CLMMPoolInfo]:
        """
        刷新池信息并更新价格缓存。

        Args:
            force: 是否强制刷新（忽略刷新间隔）。

        Returns:
            池信息；失败时返回 None。
        """
        now = time.time()
        interval = self._settings.refresh_interval_sec
        if not force and interval > 0 and (now - self._last_update_ts) < interval:
            self._update_price_from_feed()
            return self._pool_info

        try:
            pool_info = await self._connectors[self._exchange].get_pool_info(
                trading_pair=self._trading_pair
            )
            self._pool_info = pool_info
            pool_price = Decimal(str(pool_info.price)) if pool_info is not None else None
            self._price = self._pick_price(pool_price)
            self._last_update_ts = now
            return pool_info
        except Exception as e:
            self._pool_info = None
            self._price = self._pick_price(None)
            self._log(logging.ERROR, f"PoolInfoFeed refresh error: {e}")
            return None

    def _normalized_source(self) -> PriceSource:
        """
        规范化价格来源名称。

        Returns:
            价格来源字符串。
        """
        source = (self._settings.price_source or "pool_info").lower()
        if source not in self.SUPPORTED_SOURCES:
            self._log(logging.WARNING, f"Unsupported price_source={source}, fallback to pool_info.")
            return "pool_info"
        return source

    def _pick_price(self, pool_price: Optional[Decimal]) -> Optional[Decimal]:
        """
        根据配置选择价格来源。

        Args:
            pool_price: pool-info 返回的价格。

        Returns:
            选定的价格。
        """
        if self._normalized_source() == "amm_gateway":
            self._update_price_from_feed()
            if self._price is not None:
                return self._price
        return pool_price

    def _ensure_amm_feed_started(self) -> None:
        """
        确保 AmmGatewayDataFeed 已就绪并启动。
        """
        if self._normalized_source() != "amm_gateway":
            return
        connector = self._settings.price_connector
        if not connector:
            self._log(logging.WARNING, "price_source=amm_gateway but price_connector is empty.")
            return

        interval = self._amm_update_interval()
        need_rebuild = (
            self._amm_feed is None
            or connector != self._amm_feed_connector
            or self._settings.price_order_amount_in_base != self._amm_feed_amount
            or interval != self._amm_feed_interval
        )
        if need_rebuild:
            self._stop_amm_feed()
            self._amm_feed = AmmGatewayDataFeed(
                connector=connector,
                trading_pairs={self._trading_pair},
                order_amount_in_base=self._settings.price_order_amount_in_base,
                update_interval=interval,
            )
            self._amm_feed_connector = connector
            self._amm_feed_amount = self._settings.price_order_amount_in_base
            self._amm_feed_interval = interval

        if self._amm_feed is not None and not self._amm_feed.started:
            self._amm_feed.start()

    def _stop_amm_feed(self) -> None:
        """
        停止并清理 AmmGatewayDataFeed。
        """
        if self._amm_feed is not None and self._amm_feed.started:
            self._amm_feed.stop()
        self._amm_feed = None
        self._amm_feed_connector = None
        self._amm_feed_amount = None
        self._amm_feed_interval = None

    def _amm_update_interval(self) -> float:
        """
        计算 AmmGatewayDataFeed 的刷新间隔。

        Returns:
            刷新间隔秒数。
        """
        interval = float(self._settings.refresh_interval_sec)
        if interval <= 0:
            return 1.0
        return interval

    def _update_price_from_feed(self) -> None:
        """
        使用 AmmGatewayDataFeed 更新价格缓存（如可用）。
        """
        self._ensure_amm_feed_started()
        if self._amm_feed is None or not self._amm_feed.is_ready():
            return
        price_info = self._amm_feed.price_dict.get(self._trading_pair)
        if price_info is None:
            return
        self._price = (price_info.buy_price + price_info.sell_price) / Decimal("2")
