from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, Optional, Tuple

from .components import BalanceEvent, BalanceEventKind


DEFAULT_RECONCILE_EPSILON = Decimal("1e-8")


@dataclass(frozen=True)
class LedgerStatus:
    has_balance: bool
    is_recent: bool
    is_reconciled: bool
    needs_reconcile: bool

    @property
    def allow_actions(self) -> bool:
        return self.has_balance and (self.is_recent or self.is_reconciled)


class BalanceLedger:
    def __init__(
        self,
        *,
        window_sec: float = 120.0,
        epsilon: Decimal = DEFAULT_RECONCILE_EPSILON,
        logger=None,
    ) -> None:
        self._window_sec = float(max(0.0, window_sec))
        self._epsilon = max(Decimal("0"), epsilon)
        self._logger = logger

        self._balance_base = Decimal("0")
        self._balance_quote = Decimal("0")
        self._has_balance = False
        self._last_event_ts = 0.0
        self._seen_event_ids: set[str] = set()
        self._last_event_by_key: Dict[Tuple[str, BalanceEventKind], float] = {}
        self._pending_events: Dict[str, BalanceEvent] = {}

    @property
    def balance_base(self) -> Decimal:
        return self._balance_base

    @property
    def balance_quote(self) -> Decimal:
        return self._balance_quote

    @property
    def has_balance(self) -> bool:
        return self._has_balance

    @property
    def last_event_ts(self) -> float:
        return self._last_event_ts

    def has_event(self, executor_id: str, kind: BalanceEventKind, since_ts: float) -> bool:
        if since_ts <= 0:
            return False
        key = (executor_id, kind)
        ts = self._last_event_by_key.get(key)
        if ts is None:
            return False
        return ts >= since_ts

    def update(
        self,
        *,
        events: Iterable[BalanceEvent],
        snapshot_base: Decimal,
        snapshot_quote: Decimal,
        snapshot_fresh: bool,
        now: float,
    ) -> LedgerStatus:
        if not self._has_balance:
            if snapshot_fresh:
                self._set_balance(snapshot_base, snapshot_quote)
                pending = list(self._pending_events.values())
                self._pending_events.clear()
                self._apply_events(pending)
            else:
                self._stash_events(events)
                return LedgerStatus(False, False, False, False)

        self._apply_events(events)

        is_recent = self._is_recent(now)
        is_reconciled = False
        if self._has_balance and not is_recent and snapshot_fresh:
            is_reconciled = self._reconcile(snapshot_base, snapshot_quote)
        needs_reconcile = self._has_balance and not is_recent and not is_reconciled
        return LedgerStatus(self._has_balance, is_recent, is_reconciled, needs_reconcile)

    def force_reset(self, *, snapshot_base: Decimal, snapshot_quote: Decimal, now: float) -> None:
        self._set_balance(snapshot_base, snapshot_quote)
        self._last_event_ts = now
        self._pending_events.clear()
        self._last_event_by_key.clear()

    def _stash_events(self, events: Iterable[BalanceEvent]) -> None:
        for event in events:
            if event.event_id in self._seen_event_ids or event.event_id in self._pending_events:
                continue
            self._pending_events[event.event_id] = event

    def _set_balance(self, base: Decimal, quote: Decimal) -> None:
        self._balance_base = base
        self._balance_quote = quote
        self._has_balance = True

    def _apply_events(self, events: Iterable[BalanceEvent]) -> None:
        ordered = sorted(events, key=lambda event: (event.timestamp, event.event_id))
        for event in ordered:
            if event.event_id in self._seen_event_ids:
                continue
            if self._last_event_ts > 0 and event.timestamp < self._last_event_ts:
                continue
            self._balance_base += event.delta_base
            self._balance_quote += event.delta_quote
            self._seen_event_ids.add(event.event_id)
            self._last_event_ts = max(self._last_event_ts, event.timestamp)
            self._last_event_by_key[(event.executor_id, event.kind)] = event.timestamp

    def _is_recent(self, now: float) -> bool:
        if self._last_event_ts <= 0 or self._window_sec <= 0:
            return False
        return (now - self._last_event_ts) <= self._window_sec

    def _reconcile(self, snapshot_base: Decimal, snapshot_quote: Decimal) -> bool:
        base_diff = abs(self._balance_base - snapshot_base)
        quote_diff = abs(self._balance_quote - snapshot_quote)
        if base_diff <= self._epsilon and quote_diff <= self._epsilon:
            self._balance_base = snapshot_base
            self._balance_quote = snapshot_quote
            return True
        return False
