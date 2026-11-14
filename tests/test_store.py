import json
import sqlite3

from app.dedupe import group_instances
from app.normalize import normalize_record
from app.repository import find_dataset, list_datasets
from app.store import Store

MERGE_CONFIG = {
    "title_similarity_threshold": 0.86,
    "require_same_publisher": True,
    "require_same_period": True,
    "require_same_geography": True,
    "field_priority": ["alpha", "beta"],
}

MERGE_RULES = {
    "categories": {"environment": ["environment", "environmental"]},
    "frequency": {"daily": ["daily", "every day"]},
    "geography": {"US": ["united states", "us"]},
}


def _merge_group():
    def build(source, url, title, category, frequency):
        record = {
            "source": source,
            "source_url": url,
            "title": title,
            "publisher": "Atlas Environmental Agency",
            "temporal": "2023-01-01 to 2023-12-31",
            "category": category,
            "update_frequency": frequency,
            "geography": "United States",
            "license": "CC BY 4.0",
            "fields": [{"name": "station_id", "type": "string"}],
            "resources": [{"format": "CSV", "size_raw": "1 MB", "url": "u"}],
        }
        normalized, transforms = normalize_record(record, MERGE_RULES)
        normalized["instance_id"] = f"inst_{source}_merge"
        normalized["transforms"] = transforms
        return normalized

    alpha = build("alpha", "http://127.0.0.1:5000/alpha/air",
                  "Air Quality Monitoring Stations 2023", "Environment", "Daily")
    beta = build("beta", "http://127.0.0.1:5000/beta/air",
                 "Air Quality Monitoring Stations (2023)", "Environmental Monitoring Network",
                 "Continuous-ish")
    assert beta["category"] == "environmental-monitoring-network"
    groups = group_instances([beta, alpha], MERGE_CONFIG)
    assert len(groups) == 1
    return groups[0]


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


def test_merge_decision_detail_persists_degradation_lineage_and_round_trips(tmp_path):
    db_path = tmp_path / "archive.db"
    group = _merge_group()
    assert group["inherited_degraded_fields"] == ["category", "update_frequency"]
    first_uid, first_fp = group["dataset_uid"], group["fingerprint"]
    store = Store(db_path)
    try:
        first = store.persist_groups("run_a", [group])
        # 同组二次写入：谱系不参与 fingerprint，必须仍然幂等
        second = store.persist_groups("run_b", [_merge_group()])
    finally:
        store.close()
    assert first["merged"] == 1
    assert (second["new"], second["updated"], second["unchanged"], second["merged"]) == (0, 0, 1, 1)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT detail_json FROM merge_decisions WHERE dataset_uid=?",
                           (first_uid,)).fetchone()
        assert row is not None
        detail = json.loads(row[0])
    finally:
        conn.close()

    assert detail["inherited_degraded_fields"] == ["category", "update_frequency"]
    lineage = {member["source"]: member for member in detail["member_lineage"]}
    assert set(lineage) == {"alpha", "beta"}
    assert lineage["alpha"]["is_primary"] is True
    beta_category = lineage["beta"]["fields"]["category"]
    assert beta_category["status"] == "warn"
    assert beta_category["rule"] == "category.synonym"
    assert beta_category["raw_value"] == "Environmental Monitoring Network"
    assert beta_category["normalized_value"] == "environmental-monitoring-network"
    assert lineage["alpha"]["fields"]["category"]["status"] == "applied"
    assert lineage["beta"]["fields"]["update_frequency"]["status"] == "warn"

    # repository 快照能查回完整谱系与数据集层标记
    dataset = find_dataset(str(db_path), first_uid)
    assert dataset is not None
    assert dataset["inherited_degraded_fields"] == ["category", "update_frequency"]
    decision = dataset["merge_decision"]
    assert decision["detail"]["member_lineage"] == detail["member_lineage"]
    # 数据集层字段保持主来源规范值
    assert dataset["category"] == "environment"
    assert dataset["update_frequency"] == "daily"
    assert dataset["fingerprint"] == first_fp
