from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .models import (
    DerivativesPositioningSnapshot,
    ExternalSignal,
    MarketSnapshot,
    MarketType,
    TradeSide,
)


class MarketDataPort(Protocol):
    name: str

    async def snapshot(
        self,
        symbol: str,
        market_type: MarketType,
        timeframe: str,
        limit: int,
    ) -> MarketSnapshot:
        """Return a normalized market snapshot with recent movement data."""

    async def latest_price(self, symbol: str, venue: str, market_type: MarketType) -> Decimal:
        """Return the latest trusted price for a normalized symbol."""


class DerivativesPositioningPort(Protocol):
    name: str

    async def positioning(
        self, symbol: str, period: str | None = None, limit: int | None = None
    ) -> DerivativesPositioningSnapshot:
        """Return read-only aggregate derivatives positioning context."""


class SocialSignalPort(Protocol):
    name: str

    async def signals(
        self, symbol: str | None, since: datetime, limit: int
    ) -> list[ExternalSignal]:
        """Return normalized social/news signals with source references."""


class ExecutionPort(Protocol):
    name: str

    async def submit(
        self,
        *,
        symbol: str,
        market_type: MarketType,
        side: TradeSide,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
    ) -> dict[str, str]:
        """Submit an order through an explicit adapter."""


class ConnectorRegistry:
    """Explicit connector registry; it never forwards arbitrary HTTP requests."""

    def __init__(self) -> None:
        self._connectors: dict[str, object] = {}

    def register(self, connector: object) -> None:
        name = getattr(connector, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError("connectors must expose a non-empty name")
        if name in self._connectors:
            raise ValueError(f"connector already registered: {name}")
        self._connectors[name] = connector

    def health(self) -> dict[str, str]:
        return {name: type(connector).__name__ for name, connector in self._connectors.items()}
