"""Guard against false eligibility without making any network calls."""
from copy import deepcopy
import check_exchange_feasibility as feasibility


def test_partial_pagination_cannot_pass(monkeypatch):
    target = 1_000_000_000
    monkeypatch.setattr(feasibility, "now_ms", lambda: target+3*feasibility.MS_DAY)
    class Exchange:
        def __init__(self):
            self.calls = 0
        def fetch_ohlcv(self, *args, **kwargs):
            self.calls += 1
            return [[target, 1, 1, 1, 1, 1]] if self.calls == 1 else []
    result = feasibility.test_pagination(Exchange(), "BTC/USDT", 100)
    assert result["pages_ok"] == 1
    assert not result["ok"]


def test_perp_blocked_or_wrong_quote_rejected():
    result = {"geo_blocked": False, "status": "OK", "perp_symbol": "BTC/USDT:USDT", "perp_quote": "USDT", "derivs": {"funding_days_ok": 365}, "hist_perp_max_ok_days": 365}
    assert feasibility.perp_eligibility(result)[0]
    for field, value in [("geo_blocked", True), ("perp_quote", "USD"), ("status", "LOI")]:
        bad = deepcopy(result)
        bad[field] = value
        assert not feasibility.perp_eligibility(bad)[0]
