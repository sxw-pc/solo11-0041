import sqlite3

from app.repository import list_datasets
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
