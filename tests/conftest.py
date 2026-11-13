from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from app.normalize import load_rules
from app.pipeline import load_sources
from app.server import create_app

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
FIXTURES_DIR = ROOT / "fixtures"


@pytest.fixture(scope="session")
def config_dir() -> Path:
    return CONFIG_DIR


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def rules() -> dict:
    return load_rules(str(CONFIG_DIR / "normalize_rules.yaml"))


@pytest.fixture()
def mock_server(tmp_path: Path):
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    data_dir = tmp_path / "data"
    db_path = data_dir / "datasheet_probe.db"
    app = create_app(db_path=db_path, data_dir=data_dir, config_dir=CONFIG_DIR)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    info = {
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "app": app,
        "db_path": str(db_path),
        "data_dir": str(data_dir),
    }
    try:
        yield info
    finally:
        server.shutdown()
        thread.join(timeout=3)


@pytest.fixture()
def sources(mock_server):
    return load_sources(CONFIG_DIR, base_url=mock_server["base_url"])


@pytest.fixture()
def fixture_reader(fixtures_dir: Path):
    def read(portal: str, filename: str) -> str:
        return (fixtures_dir / f"portal_{portal}" / filename).read_text(encoding="utf-8")

    return read
