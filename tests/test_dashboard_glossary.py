"""Test C2.08: mọi cột hiển thị trong dashboard đều có giải thích cho người xem."""
import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_app_glossary", ROOT / "dashboard" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

SHOWN = {
    "quotes": app.QUOTE_COLS,
    "live": app.LIVE_SHOWN_COLS,
    "rank": app.RANK_COLS,
    "gap_summary": app.GAP_SUMMARY_COLS,
    "gap_events": app.GAP_EVENT_COLS,
}


def test_every_shown_column_has_a_plain_explanation():
    for table, cols in SHOWN.items():
        for col in cols:
            text = app.GLOSSARY.get(col, "")
            assert len(text) > 15, f"{table}.{col} chưa có giải thích"


def test_col_config_covers_exactly_the_requested_columns():
    cfg = app.col_config(app.RANK_COLS)
    assert list(cfg) == app.RANK_COLS


def test_live_table_columns_match_what_live_latest_returns():
    grid = pd.date_range("2026-10-20 10:00", periods=2, freq="5s", tz="UTC")
    live = pd.DataFrame(dict(ts_recv_utc=grid + pd.Timedelta(seconds=1), ts_grid_utc=grid,
                             venue=["a_spot", "a_spot"], market_type=["spot", "spot"], bid=[1.0, 2.0], ask=[1.1, 2.1],
                             mid=[1.05, 2.05], spread_abs=[0.1, 0.1], spread_bps=[1.0, 2.0],
                             depth_bid_10bps_usdt=[1.0, 2.0], depth_ask_10bps_usdt=[1.0, 2.0],
                             depth_truncated=[False, True], data_kind="live"))
    assert list(app.live_latest(live).columns) == app.LIVE_SHOWN_COLS