import sqlite3

import pytest

from app.repository import get_run, list_datasets, snapshot
from app.store import Store


def _instance():
    return {
        "instance_id": "inst_alpha_demo",
        "source": "alpha",
        "source_url": "http://127.0.0.1:5000/alpha/dataset/demo",
        "source_item_id": "demo",
        "title": "Demo Dataset",
        "category": "environment",
        "publisher": "Atlas Environmental Agency",
        "temporal_start": "2023-01-01",
        "temporal_end": "2023-12-31",
        "geography": "US",
        "license": "CC-BY-4.0",
        "update_frequency": "daily",
        "fields": [{"name": "value", "type": "number", "description": "value",
                    "source_selector": "table tr", "location": "row 1"}],
        "resources": [{"format": "CSV", "size_bytes": 1024, "size_raw": "1 KB", "url": "u",
                       "source_selector": "li", "location": "row 1"}],
        "extraction": {"fallback_used": False, "fields": {}, "lists": {}},
        "transforms": [{"field_name": "category", "rule": "category.synonym", "before_value": "Environment",
                        "after_value": "environment", "status": "applied"}],
        "findings": [{"instance_id": "inst_alpha_demo", "field_name": "license", "severity": "warn",
                      "rule_id": "demo.rule", "evidence": "demo evidence"}],
    }


def _group(fingerprint="fp1", title="Demo Dataset"):
    instance = _instance()
    return {
        "dataset_uid": "ds_demo_store",
        "title": title,
        "title_norm": "demo dataset",
        "category": "environment",
        "publisher": "Atlas Environmental Agency",
        "publisher_norm": "atlas environmental agency",
        "temporal_start": "2023-01-01",
        "temporal_end": "2023-12-31",
        "geography": "US",
        "license": "CC-BY-4.0",
        "update_frequency": "daily",
        "fields": instance["fields"],
        "resources": instance["resources"],
        "instances": [instance],
        "source_count": 1,
        "fingerprint": fingerprint,
        "findings": [],
    }


def test_persist_is_idempotent_and_reports_four_counts(tmp_path):
    db_path = tmp_path / "archive.db"
    store = Store(db_path)
    try:
        first = store.persist_groups("run_a", [_group()])
        second = store.persist_groups("run_b", [_group()])
        updated = store.persist_groups("run_c", [_group(fingerprint="fp2", title="Demo Dataset Updated")])
    finally:
        store.close()

    assert (first["new"], first["updated"], first["unchanged"], first["merged"]) == (1, 0, 0, 0)
    assert (second["new"], second["updated"], second["unchanged"], second["merged"]) == (0, 0, 1, 0)
    assert (updated["new"], updated["updated"], updated["unchanged"], updated["merged"]) == (0, 1, 0, 0)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM dataset_instance").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM field").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM resource").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM field_transform").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM validation_finding").fetchone()[0] == 1
        assert conn.execute("SELECT title FROM dataset").fetchone()[0] == "Demo Dataset Updated"
    finally:
        conn.close()


def test_read_queries_filter_by_category_publisher_and_severity(tmp_path):
    db_path = tmp_path / "archive.db"
    store = Store(db_path)
    try:
        store.persist_groups("run_a", [_group()])
    finally:
        store.close()
    assert len(list_datasets(str(db_path), category="environment")) == 1
    assert len(list_datasets(str(db_path), category="transportation")) == 0
    assert len(list_datasets(str(db_path), publisher="Atlas Environmental Agency")) == 1
    assert len(list_datasets(str(db_path), updated_from="2000-01-01")) == 1
    assert len(list_datasets(str(db_path), updated_to="2000-01-01")) == 0
    assert len(list_datasets(str(db_path), severity="warn")) == 1
    assert len(list_datasets(str(db_path), severity="error")) == 0
    rows = list_datasets(str(db_path), category="environment")
    assert rows[0]["sources"] == ["alpha"]
    assert rows[0]["finding_count"] == 1


def _archive_args(outcome: str) -> tuple:
    status = 200 if outcome == "ok" else 503
    return (
        "run_a", "alpha", f"http://127.0.0.1:5000/alpha/{outcome}", "detail",
        f"/tmp/{outcome}.html", status, 100, "2026-11-14T00:00:00+00:00",
        "sha_" + outcome, "text/html",
    )


def test_archive_raw_separates_ok_and_failed_outcomes(tmp_path):
    db_path = tmp_path / "archive.db"
    store = Store(db_path)
    try:
        store.record_run(
            "run_a", "alpha", "http", "partial",
            "2026-11-14T00:00:00+00:00", "2026-11-14T00:00:01+00:00", 1, 2, 5,
        )
        store.archive_raw(*_archive_args("ok"), outcome="ok")
        store.archive_raw(*_archive_args("failed"), outcome="failed")
        with pytest.raises(ValueError, match="未知留档结果"):
            store.archive_raw(*_archive_args("ok"), outcome="bogus")
    finally:
        store.close()

    conn = sqlite3.connect(db_path)
    try:
        rows = dict(
            conn.execute("SELECT outcome, COUNT(*) FROM raw_archive GROUP BY outcome").fetchall()
        )
        assert rows == {"ok": 1, "failed": 1}
    finally:
        conn.close()

    run = get_run(str(db_path), "run_a")
    assert run["counts"]["raw_pages"] == 1
    assert run["counts"]["raw_failures"] == 1
    stats = snapshot(str(db_path))["stats"]
    assert stats["raw_archive_count"] == 1
    assert stats["raw_failure_count"] == 1


def test_store_migrates_legacy_raw_archive_without_outcome(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE raw_archive (
            raw_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            url TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            byte_count INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            content_type TEXT,
            UNIQUE (run_id, url, kind)
        )"""
    )
    conn.execute(
        """INSERT INTO raw_archive
        (raw_id, run_id, source, url, kind, file_path, status_code, byte_count, fetched_at,
        sha256, content_type)
        VALUES ('raw_legacy', 'run_old', 'alpha', 'http://127.0.0.1:5000/alpha/legacy',
        'detail', '/tmp/legacy.html', 200, 10, '2026-01-01T00:00:00+00:00', 'sha', 'text/html')"""
    )
    conn.commit()
    conn.close()

    store = Store(db_path)  # 打开旧库应补齐 outcome 列
    try:
        store.archive_raw(*_archive_args("failed"), outcome="failed")
    finally:
        store.close()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(raw_archive)")}
        assert "outcome" in columns
        legacy = conn.execute(
            "SELECT outcome FROM raw_archive WHERE raw_id='raw_legacy'"
        ).fetchone()[0]
        assert legacy == "ok"  # 旧行按成功留档回填
        failed = conn.execute(
            "SELECT outcome FROM raw_archive WHERE url LIKE '%/failed'"
        ).fetchone()[0]
        assert failed == "failed"
    finally:
        conn.close()
