from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .models import DEFAULT_ACCOUNT_ID


def _decimal(name: str, default: str) -> Decimal:
    value = os.getenv(name, default)
    try:
        return Decimal(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a decimal, got {value!r}") from exc


def _integer(name: str, default: str) -> int:
    value = os.getenv(name, default)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _bounded_integer(name: str, default: str, minimum: int, maximum: int) -> int:
    value = _integer(name, default)
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}, got {value!r}"
        )
    return value


def _positive_float(name: str, default: str) -> float:
    value = os.getenv(name, default)
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero, got {value!r}")
    return parsed


def _boolean(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {value!r}")


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = "development"
    db_path: Path = Path("data/trading.db")
    paper_trading_only: bool = True
    max_order_notional: Decimal = Decimal("1000")
    max_operation_loss: Decimal = Decimal("25")
    max_daily_loss: Decimal = Decimal("100")
    max_leverage: Decimal = Decimal("3")
    max_open_operations: int = 10
    max_signal_age_seconds: int = 30
    execution_plan_read_freshness_seconds: int = 300
    default_fee_rate: Decimal = Decimal("0.001")
    default_slippage_rate: Decimal = Decimal("0.0002")
    http_timeout_seconds: float = 10.0
    binance_market_data_enabled: bool = True
    binance_spot_base_url: str = "https://data-api.binance.vision"
    binance_futures_base_url: str = "https://fapi.binance.com"
    binance_positioning_period: str = "5m"
    binance_positioning_limit: int = 30
    news_rss_feeds: tuple[str, ...] = ("https://cointelegraph.com/rss",)
    cryptopanic_api_enabled: bool = False
    cryptopanic_auth_token: str = ""
    cryptopanic_api_plan: str = "growth"
    x_api_enabled: bool = False
    x_api_base_url: str = "https://api.x.com"
    x_bearer_token: str = ""
    x_influencer_usernames: tuple[str, ...] = ()
    telegram_api_base_url: str = "https://api.telegram.org"
    account_bootstrap_token: str = ""
    credential_encryption_key: str = ""
    default_account_id: str = DEFAULT_ACCOUNT_ID
    mcp_allowed_hosts: tuple[str, ...] = (
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "testserver",
        "testserver:*",
    )
    mcp_allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            db_path=Path(os.getenv("TRADING_DB_PATH", "data/trading.db")),
            paper_trading_only=_boolean("PAPER_TRADING_ONLY", "true"),
            max_order_notional=_decimal("MAX_ORDER_NOTIONAL", "1000"),
            max_operation_loss=_decimal("MAX_OPERATION_LOSS", "25"),
            max_daily_loss=_decimal("MAX_DAILY_LOSS", "100"),
            max_leverage=_decimal("MAX_LEVERAGE", "3"),
            max_open_operations=_integer("MAX_OPEN_OPERATIONS", "10"),
            max_signal_age_seconds=_integer("MAX_SIGNAL_AGE_SECONDS", "30"),
            execution_plan_read_freshness_seconds=_bounded_integer(
                "EXECUTION_PLAN_READ_FRESHNESS_SECONDS", "300", 1, 24 * 60 * 60
            ),
            default_fee_rate=_decimal("DEFAULT_FEE_RATE", "0.001"),
            default_slippage_rate=_decimal("DEFAULT_SLIPPAGE_RATE", "0.0002"),
            http_timeout_seconds=_positive_float("HTTP_TIMEOUT_SECONDS", "10"),
            binance_market_data_enabled=_boolean("BINANCE_MARKET_DATA_ENABLED", "true"),
            binance_spot_base_url=os.getenv(
                "BINANCE_SPOT_BASE_URL", "https://data-api.binance.vision"
            ),
            binance_futures_base_url=os.getenv(
                "BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com"
            ),
            binance_positioning_period=os.getenv(
                "BINANCE_POSITIONING_PERIOD", "5m"
            ).strip().lower(),
            binance_positioning_limit=_bounded_integer(
                "BINANCE_POSITIONING_LIMIT", "30", 1, 500
            ),
            news_rss_feeds=_csv(
                "NEWS_RSS_FEEDS", "https://cointelegraph.com/rss"
            ),
            cryptopanic_api_enabled=_boolean("CRYPTOPANIC_API_ENABLED", "false"),
            cryptopanic_auth_token=os.getenv("CRYPTOPANIC_AUTH_TOKEN", "").strip(),
            cryptopanic_api_plan=os.getenv("CRYPTOPANIC_API_PLAN", "growth").strip().lower(),
            x_api_enabled=_boolean("X_API_ENABLED", "false"),
            x_api_base_url=os.getenv("X_API_BASE_URL", "https://api.x.com"),
            x_bearer_token=os.getenv("X_BEARER_TOKEN", "").strip(),
            x_influencer_usernames=_csv("X_INFLUENCER_USERNAMES", ""),
            telegram_api_base_url=os.getenv(
                "TELEGRAM_API_BASE_URL", "https://api.telegram.org"
            ).strip(),
            account_bootstrap_token=os.getenv("ACCOUNT_BOOTSTRAP_TOKEN", "").strip(),
            credential_encryption_key=os.getenv("CREDENTIAL_ENCRYPTION_KEY", "").strip(),
            default_account_id=os.getenv("DEFAULT_ACCOUNT_ID", DEFAULT_ACCOUNT_ID).strip(),
            mcp_allowed_hosts=_csv(
                "MCP_ALLOWED_HOSTS",
                "localhost,localhost:*,127.0.0.1,127.0.0.1:*,testserver,testserver:*",
            ),
            mcp_allowed_origins=_csv("MCP_ALLOWED_ORIGINS", ""),
        )
