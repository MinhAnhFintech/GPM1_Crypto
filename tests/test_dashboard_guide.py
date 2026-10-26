"""Test C2.08: khung Hướng dẫn xem ở thanh bên khớp với các thẻ thật của dashboard."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_app_guide", ROOT / "dashboard" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_guide_lists_every_tab_once_in_tab_order():
    assert [name for name, _ in app.GUIDE_STEPS] == app.TAB_LABELS
    assert len(set(app.TAB_LABELS)) == len(app.TAB_LABELS) == 8


def test_every_guide_step_is_a_short_sentence():
    for name, text in app.GUIDE_STEPS:
        assert 10 < len(text) < 120, name