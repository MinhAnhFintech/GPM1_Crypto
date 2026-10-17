from pathlib import Path
import pytest
from src.utils.config import load_config
from src.utils.synthetic import make_fixture

ROOT = Path(__file__).resolve().parents[1]


def pytest_addoption(parser):
    parser.addoption("--require-tv5", action="store_true", help="Fail, rather than skip, if TV5 IS/CS/ILS modules are absent")


@pytest.fixture(scope="session")
def cfg():
    return load_config(ROOT/"config/config.example.yaml")


@pytest.fixture(scope="session")
def multi(cfg):
    return make_fixture(cfg, "multi")
