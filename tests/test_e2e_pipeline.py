import hashlib
import json
import sqlite3
from pathlib import Path

from app.pipeline import run_pipeline


def test_full_http_pipeline_against_local_mock_portal(tmp_path, mock_server, config_dir):
    data_dir = tmp_path / "run_data"
    db_path = tmp_path / "run_data" / "archive.db"
    result = run_pipeline(
        "all",
        base_url=mock_server["base_url"],
        db_path=db_path,
        data_dir=data_dir,
        config_dir=config_dir,
    )
    assert result["errors"] == []
    assert result["raw_pages"] == 22
    assert result["counts"] == {
        "new": 15,
        "updated": 0,
        "unchanged": 0,
        "merged": 1,
        "datasets": 15,
        "instances": 16,
        "findings": 13,
        "transforms": 184,
    }
    assert Path(result["export_dir"], "archive.json").exists()
    archive = json.loads(Path(result["export_dir"], "archive.json").read_text(encoding="utf-8"))
    assert archive["stats"]["finding_counts"] == {"error": 2, "warn": 10, "info": 1}

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 15
        assert conn.execute("SELECT COUNT(*) FROM dataset_instance").fetchone()[0] == 16
        assert conn.execute("SELECT COUNT(*) FROM raw_archive").fetchone()[0] == 22
        runs = conn.execute("SELECT source, status, pages, discovered FROM crawl_run").fetchall()
        assert runs == [("alpha", "ok", 2, 5), ("beta", "ok", 2, 5), ("gamma", "ok", 2, 6)]
        raw_rows = conn.execute("SELECT url, file_path, sha256 FROM raw_archive").fetchall()
        for url, file_path, expected in raw_rows:
            assert url.startswith(mock_server["base_url"])
            body = Path(file_path).read_bytes()
            assert hashlib.sha256(body).hexdigest() == expected
    finally:
        conn.close()

    second = run_pipeline(
        "all",
        base_url=mock_server["base_url"],
        db_path=db_path,
        data_dir=data_dir,
        config_dir=config_dir,
    )
    assert second["errors"] == []
    assert second["counts"]["new"] == 0
    assert second["counts"]["updated"] == 0
    assert second["counts"]["unchanged"] == 15
    assert second["counts"]["merged"] == 1
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 15
        assert conn.execute("SELECT COUNT(*) FROM raw_archive").fetchone()[0] == 44
    finally:
        conn.close()
