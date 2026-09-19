from __future__ import annotations

from .account_context import account_scope
from .accounts import AccountManager
from .config import Settings
from .mcp_server import build_mcp_server
from .notifications import NotificationService
from .service import TradingService
from .store import SQLiteStore


def run() -> None:
    """Run the MCP server over stdio for local MCP hosts."""

    settings = Settings.from_env()
    store = SQLiteStore(settings.db_path)
    account_manager = AccountManager(settings, store)
    notification_service = NotificationService(settings, account_manager)
    service = TradingService(
        settings,
        store,
        account_manager=account_manager,
        default_account_id=settings.default_account_id,
        notification_service=notification_service,
    )
    server = build_mcp_server(service)
    with account_scope(settings.default_account_id):
        server.run(transport="stdio")


if __name__ == "__main__":
    run()
