from __future__ import annotations

from fastapi.testclient import TestClient

from crypto_orchestrator.api import create_app
from crypto_orchestrator.config import Settings


def test_ui_is_public_and_serves_same_origin_assets(tmp_path) -> None:
    app = create_app(Settings(db_path=tmp_path / "ui.db"))

    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/app.js")
        styles = client.get("/styles.css")
        protected = client.get("/api/v1/account")

    assert page.status_code == 200
    assert "Create or open an account" in page.text
    assert script.status_code == 200
    assert "sessionStorage" in script.text
    assert 'stepSelect(\n          "Symbol or context variable"' in script.text
    assert "Use operation symbol ($context.symbol)" in script.text
    assert "plan-symbol-options" in page.text
    assert "selectedPlanSymbols" in script.text
    assert '"ZEC/USDT", "ZEC/USDT"' in script.text
    for symbol in ("LTC", "LINK", "AVAX", "DOT", "UNI", "AAVE", "TRX"):
        assert f'"{symbol}/USDT", "{symbol}/USDT"' in script.text
    assert "Cycle history" in page.text
    assert "/api/v1/strategy/cycles" in script.text
    assert styles.status_code == 200
    assert protected.status_code == 401
