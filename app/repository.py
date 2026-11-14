"""只读查询与谱系档案快照。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_datasets(
    db_path: str,
    *,
    category: str | None = None,
    publisher: str | None = None,
    temporal_from: str | None = None,
    temporal_to: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    severity: str | None = None,
    limit: int = 200,
) -> list[dict]:
    where, params = ["1=1"], []
    if category:
        where.append("LOWER(d.category) = LOWER(?)")
        params.append(category)
    if publisher:
        where.append("LOWER(d.publisher) = LOWER(?)")
        params.append(publisher)
    if temporal_from:
        where.append("d.temporal_end >= ?")
        params.append(temporal_from)
    if temporal_to:
        where.append("d.temporal_start <= ?")
        params.append(temporal_to)
    if updated_from:
        where.append("d.updated_at >= ?")
        params.append(updated_from)
    if updated_to:
        where.append("d.updated_at <= ?")
        params.append(updated_to)
    if severity:
        where.append(
            "EXISTS (SELECT 1 FROM validation_finding f WHERE f.dataset_uid=d.dataset_uid AND f.severity=?)"
        )
        params.append(severity)
    params.append(max(1, min(1000, int(limit))))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"""SELECT d.*, GROUP_CONCAT(DISTINCT i.source) AS source_list,
            (SELECT COUNT(*) FROM validation_finding f WHERE f.dataset_uid=d.dataset_uid) AS finding_count
            FROM dataset d LEFT JOIN dataset_instance i ON i.dataset_uid=d.dataset_uid
            WHERE {' AND '.join(where)}
            GROUP BY d.dataset_uid ORDER BY d.category, d.title LIMIT ?""",
            params,
        ).fetchall()
    finally:
        conn.close()
    output = []
    for row in rows:
        item = dict(row)
        item["sources"] = sorted(filter(None, str(item.pop("source_list", "") or "").split(",")))
        output.append(item)
    return output


def get_run(db_path: str, run_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        runs = [dict(row) for row in conn.execute(
            "SELECT * FROM crawl_run WHERE run_id=? ORDER BY source", (run_id,)
        ).fetchall()]
        if not runs:
            return None
        counts = {
            "datasets": conn.execute("SELECT COUNT(*) FROM dataset WHERE last_run_id=?", (run_id,)).fetchone()[0],
            "raw_pages": conn.execute("SELECT COUNT(*) FROM raw_archive WHERE run_id=?", (run_id,)).fetchone()[0],
            "findings": conn.execute("SELECT COUNT(*) FROM validation_finding WHERE run_id=?", (run_id,)).fetchone()[0],
        }
    finally:
        conn.close()
    return {"run_id": run_id, "sources": runs, "counts": counts}


def _dataset_findings(conn: sqlite3.Connection, dataset_uid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM validation_finding WHERE dataset_uid=? ORDER BY CASE severity "
        "WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, field_name",
        (dataset_uid,),
    ).fetchall()
    return [dict(row) for row in rows]


def _dataset_instances(conn: sqlite3.Connection, dataset_uid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM dataset_instance WHERE dataset_uid=? ORDER BY source", (dataset_uid,)
    ).fetchall()
    instances = []
    for row in rows:
        item = dict(row)
        item["extraction"] = _loads(item.get("extraction_json"), {})
        item.pop("extraction_json", None)
        instances.append(item)
    return instances


def _provenance(conn: sqlite3.Connection, instances: list[dict], run_id: str | None = None) -> list[dict]:
    output = []
    for instance in instances:
        if run_id:
            raw = conn.execute(
                "SELECT run_id, kind, file_path, status_code, byte_count, fetched_at, sha256, content_type "
                "FROM raw_archive WHERE url=? AND run_id=? ORDER BY fetched_at DESC LIMIT 1",
                (instance.get("source_url"), run_id),
            ).fetchone()
        else:
            raw = conn.execute(
                "SELECT run_id, kind, file_path, status_code, byte_count, fetched_at, sha256, content_type "
                "FROM raw_archive WHERE url=? ORDER BY fetched_at DESC LIMIT 1",
                (instance.get("source_url"),),
            ).fetchone()
        output.append(
            {
                "source": instance.get("source"),
                "url": instance.get("source_url"),
                "raw_archive": dict(raw) if raw else None,
            }
        )
    return output


def _merge_decision(conn: sqlite3.Connection, dataset_uid: str) -> dict | None:
    row = conn.execute("SELECT * FROM merge_decisions WHERE dataset_uid=?", (dataset_uid,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["member_refs"] = _loads(item.get("member_refs"), [])
    item["detail"] = _loads(item.get("detail_json"), {})
    item.pop("detail_json", None)
    return item


def find_dataset(db_path: str, dataset_uid: str, run_id: str | None = None) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM dataset WHERE dataset_uid=?", (dataset_uid,)).fetchone()
        if not row:
            return None
        dataset = dict(row)
        dataset["fields"] = [dict(item) for item in conn.execute(
            "SELECT * FROM field WHERE dataset_uid=? ORDER BY ordinal", (dataset_uid,)
        ).fetchall()]
        dataset["resources"] = [dict(item) for item in conn.execute(
            "SELECT * FROM resource WHERE dataset_uid=? ORDER BY ordinal", (dataset_uid,)
        ).fetchall()]
        dataset["instances"] = _dataset_instances(conn, dataset_uid)
        dataset["validation_findings"] = _dataset_findings(conn, dataset_uid)
        dataset["merge_decision"] = _merge_decision(conn, dataset_uid)
        dataset["inherited_degraded_fields"] = (
            (dataset["merge_decision"] or {}).get("detail", {}).get("inherited_degraded_fields", [])
        )
        dataset["provenance"] = _provenance(conn, dataset["instances"], run_id)
    finally:
        conn.close()
    return dataset


def snapshot(db_path: str, run_id: str | None = None) -> dict:
    where, params = ("WHERE last_run_id=?", [run_id]) if run_id else ("", [])
    conn = _connect(db_path)
    try:
        dataset_rows = conn.execute(
            f"SELECT dataset_uid FROM dataset {where} ORDER BY category, title", params
        ).fetchall()
        datasets = [find_dataset(db_path, row["dataset_uid"], run_id) for row in dataset_rows]
        raw_count = conn.execute("SELECT COUNT(*) FROM raw_archive").fetchone()[0]
        if run_id:
            raw_count = conn.execute(
                "SELECT COUNT(*) FROM raw_archive WHERE run_id=?", (run_id,)
            ).fetchone()[0]
    finally:
        conn.close()
    datasets = [item for item in datasets if item]
    category_counts: dict[str, int] = {}
    publisher_counts: dict[str, int] = {}
    severity_counts = {"error": 0, "warn": 0, "info": 0}
    field_count = resource_count = instance_count = 0
    sources: set[str] = set()
    merged = 0
    for item in datasets:
        category_counts[item.get("category") or "unknown"] = category_counts.get(item.get("category") or "unknown", 0) + 1
        publisher = item.get("publisher") or "unknown"
        publisher_counts[publisher] = publisher_counts.get(publisher, 0) + 1
        for finding in item["validation_findings"]:
            severity = finding.get("severity", "info")
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        field_count += len(item["fields"])
        resource_count += len(item["resources"])
        instance_count += len(item["instances"])
        sources.update(instance.get("source") for instance in item["instances"])
        merged += 1 if item.get("merge_decision") else 0
    stats = {
        "dataset_count": len(datasets),
        "instance_count": instance_count,
        "source_count": len([source for source in sources if source]),
        "merged_dataset_count": merged,
        "category_counts": dict(sorted(category_counts.items())),
        "publisher_counts": dict(sorted(publisher_counts.items())),
        "finding_counts": severity_counts,
        "finding_total": sum(severity_counts.values()),
        "field_count": field_count,
        "resource_count": resource_count,
        "raw_archive_count": raw_count,
        "generated_at": _now(),
        "run_id": run_id,
    }
    return {
        "schema_version": "1.0",
        "generated_at": _now(),
        "run_id": run_id,
        "stats": stats,
        "datasets": datasets,
    }
