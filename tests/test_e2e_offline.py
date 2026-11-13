import hashlib
import json
import sqlite3
from pathlib import Path

import requests

from app.pipeline import run_pipeline


def test_replay_from_static_fixtures_never_uses_http(tmp_path, fixtures_dir, config_dir, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay must not issue HTTP requests")

    monkeypatch.setattr(requests.Session, "get", forbidden)
    monkeypatch.setattr(requests, "get", forbidden)

    db_path = tmp_path / "offline.db"
    data_dir = tmp_path / "data"
    result = run_pipeline(
        "all",
        replay_from=fixtures_dir,
        db_path=db_path,
        data_dir=data_dir,
        config_dir=config_dir,
    )
    assert result["errors"] == []
    assert result["counts"]["datasets"] == 15
    assert result["counts"]["instances"] == 16
    assert result["counts"]["merged"] == 1
    assert result["raw_pages"] == 22

    archive_path = Path(result["export_dir"]) / "archive.json"
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archive["stats"]["finding_counts"] == {"error": 2, "warn": 10, "info": 1}
    assert archive["stats"]["merged_dataset_count"] == 1
    assert len(archive["datasets"]) == 15

    conn = sqlite3.connect(db_path)
    try:
        raw_rows = conn.execute("SELECT file_path, sha256 FROM raw_archive").fetchall()
        assert len(raw_rows) == 22
        for file_path, expected in raw_rows:
            body = Path(file_path).read_bytes()
            assert hashlib.sha256(body).hexdigest() == expected
            assert Path(file_path).exists()
        assert conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 15
        assert conn.execute("SELECT COUNT(*) FROM dataset_instance").fetchone()[0] == 16
        assert conn.execute("SELECT COUNT(*) FROM merge_decisions").fetchone()[0] == 1
    finally:
        conn.close()

    second = run_pipeline(
        "all",
        replay_from=fixtures_dir,
        db_path=db_path,
        data_dir=data_dir,
        config_dir=config_dir,
    )
    assert second["counts"]["new"] == 0
    assert second["counts"]["updated"] == 0
    assert second["counts"]["unchanged"] == 15
    assert second["counts"]["merged"] == 1
