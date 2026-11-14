"""SQLite 谱系档案写入：内容指纹幂等、合并决策与审计轨迹。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .dedupe import content_fingerprint
from .schema import SCHEMA_SQL


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sid(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return prefix + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """为旧库补齐 raw_archive.outcome（成功/失败留档分离口径）。"""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(raw_archive)")}
        if "outcome" not in columns:
            self.conn.execute(
                "ALTER TABLE raw_archive ADD COLUMN outcome TEXT NOT NULL DEFAULT 'ok'"
            )

    def close(self) -> None:
        self.conn.close()

    # ---------- 审计：抓取与原始留档 ----------
    def record_run(
        self,
        run_id: str,
        source: str,
        mode: str,
        status: str,
        started_at: str,
        finished_at: str,
        pages: int,
        discovered: int,
        elapsed_ms: int,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO crawl_run
            (run_id, source, mode, status, started_at, finished_at, pages, discovered, elapsed_ms, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, source) DO UPDATE SET mode=excluded.mode, status=excluded.status,
            started_at=excluded.started_at, finished_at=excluded.finished_at, pages=excluded.pages,
            discovered=excluded.discovered, elapsed_ms=excluded.elapsed_ms, error=excluded.error""",
            (run_id, source, mode, status, started_at, finished_at, pages, discovered, elapsed_ms, error),
        )
        self.conn.commit()

    def archive_raw(self, run_id: str, source: str, url: str, kind: str, file_path: str,
                    status_code: int, byte_count: int, fetched_at: str, sha256: str,
                    content_type: str | None, outcome: str = "ok") -> None:
        if outcome not in ("ok", "failed"):
            raise ValueError(f"未知留档结果: {outcome}")
        raw_id = _sid("raw_", run_id, url, kind)
        self.conn.execute(
            """INSERT INTO raw_archive
            (raw_id, run_id, source, url, kind, file_path, status_code, byte_count, fetched_at,
            sha256, content_type, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, url, kind) DO UPDATE SET file_path=excluded.file_path,
            status_code=excluded.status_code, byte_count=excluded.byte_count,
            fetched_at=excluded.fetched_at, sha256=excluded.sha256, content_type=excluded.content_type,
            outcome=excluded.outcome""",
            (raw_id, run_id, source, url, kind, file_path, status_code, byte_count,
             fetched_at, sha256, content_type, outcome),
        )
        self.conn.commit()

    # ---------- 主写入 ----------
    def persist_groups(self, run_id: str, groups: list[dict]) -> dict:
        """写入合并后的数据集；返回数据集级 新增/更新/未变/合并 四项计数。"""
        now = _now()
        new = updated = unchanged = merged = finding_count = transform_count = 0
        with self.conn:
            for group in groups:
                dataset_uid = group["dataset_uid"]
                existing = self.conn.execute(
                    "SELECT fingerprint FROM dataset WHERE dataset_uid=?", (dataset_uid,)
                ).fetchone()
                values = (
                    group["title"], group["title_norm"], group.get("category"), group.get("publisher"),
                    group.get("publisher_norm"), group.get("temporal_start"), group.get("temporal_end"),
                    group.get("geography"), group.get("license"), group.get("update_frequency"),
                    len(group.get("fields") or []), len(group.get("resources") or []),
                    group.get("source_count", 1), group["fingerprint"], run_id, now,
                )
                if existing is None:
                    self.conn.execute(
                        """INSERT INTO dataset
                        (dataset_uid, title, title_norm, category, publisher, publisher_norm, temporal_start,
                        temporal_end, geography, license, update_frequency, field_count, resource_count,
                        source_count, fingerprint, first_run_id, last_run_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (dataset_uid, *values[:14], run_id, run_id, now, now),
                    )
                    new += 1
                elif existing["fingerprint"] != group["fingerprint"]:
                    self.conn.execute(
                        """UPDATE dataset SET title=?, title_norm=?, category=?, publisher=?, publisher_norm=?,
                        temporal_start=?, temporal_end=?, geography=?, license=?, update_frequency=?,
                        field_count=?, resource_count=?, source_count=?, fingerprint=?, last_run_id=?,
                        updated_at=? WHERE dataset_uid=?""",
                        (*values, dataset_uid),
                    )
                    updated += 1
                else:
                    self.conn.execute(
                        "UPDATE dataset SET last_run_id=?, updated_at=? WHERE dataset_uid=?",
                        (run_id, now, dataset_uid),
                    )
                    unchanged += 1
                if group.get("source_count", 1) > 1:
                    merged += 1
                self._persist_instances(run_id, group, now)
                self._persist_children(group, now)
                transform_count += self._persist_transforms(run_id, group, now)
                finding_count += self._persist_findings(run_id, group, now)
                self._persist_merge_decision(run_id, group, now)
        return {
            "new": new,
            "updated": updated,
            "unchanged": unchanged,
            "merged": merged,
            "datasets": len(groups),
            "instances": sum(len(group.get("instances") or []) for group in groups),
            "findings": finding_count,
            "transforms": transform_count,
        }

    def _persist_instances(self, run_id: str, group: dict, now: str) -> None:
        for instance in group["instances"]:
            fingerprint = content_fingerprint({
                "source": instance.get("source"), "url": instance.get("source_url"),
                "title": instance.get("title"), "category": instance.get("category"),
                "publisher": instance.get("publisher"), "temporal_start": instance.get("temporal_start"),
                "temporal_end": instance.get("temporal_end"), "geography": instance.get("geography"),
                "license": instance.get("license"), "update_frequency": instance.get("update_frequency"),
                "fields": instance.get("fields"), "resources": instance.get("resources"),
            })
            self.conn.execute(
                """INSERT INTO dataset_instance
                (instance_id, dataset_uid, source, source_url, source_item_id, title, category, publisher,
                temporal_start, temporal_end, geography, license, update_frequency, field_count,
                resource_count, extraction_json, fingerprint, first_run_id, last_run_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_id) DO UPDATE SET dataset_uid=excluded.dataset_uid,
                source_item_id=excluded.source_item_id, title=excluded.title, category=excluded.category,
                publisher=excluded.publisher, temporal_start=excluded.temporal_start,
                temporal_end=excluded.temporal_end, geography=excluded.geography, license=excluded.license,
                update_frequency=excluded.update_frequency, field_count=excluded.field_count,
                resource_count=excluded.resource_count, extraction_json=excluded.extraction_json,
                fingerprint=excluded.fingerprint, last_run_id=excluded.last_run_id,
                updated_at=excluded.updated_at""",
                (
                    instance["instance_id"], group["dataset_uid"], instance.get("source"),
                    instance.get("source_url"), instance.get("source_item_id"), instance.get("title"),
                    instance.get("category"), instance.get("publisher"), instance.get("temporal_start"),
                    instance.get("temporal_end"), instance.get("geography"), instance.get("license"),
                    instance.get("update_frequency"), len(instance.get("fields") or []),
                    len(instance.get("resources") or []), _json(instance.get("extraction") or {}),
                    fingerprint, run_id, run_id, now,
                ),
            )

    def _persist_children(self, group: dict, now: str) -> None:
        dataset_uid = group["dataset_uid"]
        field_ids: list[str] = []
        for ordinal, field in enumerate(group.get("fields") or [], start=1):
            field_id = _sid("fld_", dataset_uid, ordinal)
            field_ids.append(field_id)
            fingerprint = content_fingerprint(field)
            self.conn.execute(
                """INSERT INTO field
                (field_id, dataset_uid, name, type, description, ordinal, source_selector, location,
                fingerprint, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(field_id) DO UPDATE SET name=excluded.name, type=excluded.type,
                description=excluded.description, source_selector=excluded.source_selector,
                location=excluded.location, fingerprint=excluded.fingerprint, updated_at=excluded.updated_at""",
                (field_id, dataset_uid, field.get("name"), field.get("type"), field.get("description"),
                 ordinal, field.get("source_selector"), field.get("location"), fingerprint, now),
            )
        self._delete_stale("field", "field_id", dataset_uid, field_ids)

        resource_ids: list[str] = []
        for ordinal, resource in enumerate(group.get("resources") or [], start=1):
            resource_id = _sid("res_", dataset_uid, ordinal)
            resource_ids.append(resource_id)
            fingerprint = content_fingerprint(resource)
            self.conn.execute(
                """INSERT INTO resource
                (resource_id, dataset_uid, format, size_bytes, size_raw, url, source_selector, location,
                ordinal, fingerprint, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_id) DO UPDATE SET format=excluded.format,
                size_bytes=excluded.size_bytes, size_raw=excluded.size_raw, url=excluded.url,
                source_selector=excluded.source_selector, location=excluded.location,
                fingerprint=excluded.fingerprint, updated_at=excluded.updated_at""",
                (resource_id, dataset_uid, resource.get("format"), resource.get("size_bytes"),
                 resource.get("size_raw"), resource.get("url"), resource.get("source_selector"),
                 resource.get("location"), ordinal, fingerprint, now),
            )
        self._delete_stale("resource", "resource_id", dataset_uid, resource_ids)

    def _delete_stale(self, table: str, id_column: str, dataset_uid: str, keep_ids: list[str]) -> None:
        if not keep_ids:
            self.conn.execute(f"DELETE FROM {table} WHERE dataset_uid=?", (dataset_uid,))
            return
        marks = ",".join("?" for _ in keep_ids)
        self.conn.execute(
            f"DELETE FROM {table} WHERE dataset_uid=? AND {id_column} NOT IN ({marks})",
            (dataset_uid, *keep_ids),
        )

    def _persist_transforms(self, run_id: str, group: dict, now: str) -> int:
        count = 0
        keep_ids: list[str] = []
        for instance in group["instances"]:
            for transform in instance.get("transforms") or []:
                transform_id = _sid(
                    "tr_", instance["instance_id"], transform.get("field_name"), transform.get("rule")
                )
                keep_ids.append(transform_id)
                self.conn.execute(
                    """INSERT INTO field_transform
                    (transform_id, run_id, dataset_uid, instance_id, field_name, rule, before_value,
                    after_value, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(transform_id) DO UPDATE SET run_id=excluded.run_id,
                    dataset_uid=excluded.dataset_uid, before_value=excluded.before_value,
                    after_value=excluded.after_value, status=excluded.status, created_at=excluded.created_at""",
                    (transform_id, run_id, group["dataset_uid"], instance["instance_id"],
                     transform.get("field_name"), transform.get("rule"), transform.get("before_value"),
                     transform.get("after_value"), transform.get("status"), now),
                )
                count += 1
        if keep_ids:
            marks = ",".join("?" for _ in keep_ids)
            self.conn.execute(
                f"DELETE FROM field_transform WHERE dataset_uid=? AND transform_id NOT IN ({marks})",
                (group["dataset_uid"], *keep_ids),
            )
        else:
            self.conn.execute("DELETE FROM field_transform WHERE dataset_uid=?", (group["dataset_uid"],))
        return count

    def _persist_findings(self, run_id: str, group: dict, now: str) -> int:
        dataset_uid = group["dataset_uid"]
        self.conn.execute("DELETE FROM validation_finding WHERE dataset_uid=?", (dataset_uid,))
        all_findings: list[dict] = []
        for instance in group["instances"]:
            all_findings.extend(instance.get("findings") or [])
        all_findings.extend(group.get("findings") or [])
        for finding in all_findings:
            instance_ref = finding.get("instance_id") or ""
            finding_id = _sid(
                "vf_", dataset_uid, instance_ref, finding.get("field_name"), finding.get("rule_id")
            )
            self.conn.execute(
                """INSERT OR REPLACE INTO validation_finding
                (finding_id, run_id, dataset_uid, instance_id, field_name, severity, rule_id, evidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (finding_id, run_id, dataset_uid, finding.get("instance_id"), finding.get("field_name"),
                 finding.get("severity"), finding.get("rule_id"), finding.get("evidence"), now),
            )
        return len(all_findings)

    def _persist_merge_decision(self, run_id: str, group: dict, now: str) -> None:
        dataset_uid = group["dataset_uid"]
        if group.get("source_count", 1) <= 1:
            self.conn.execute("DELETE FROM merge_decisions WHERE dataset_uid=?", (dataset_uid,))
            return
        decision = group["merge_decision"]
        decision_id = _sid("md_", dataset_uid)
        self.conn.execute(
            """INSERT INTO merge_decisions
            (decision_id, run_id, dataset_uid, member_refs, basis, score, manual_override,
            detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(decision_id) DO UPDATE SET run_id=excluded.run_id,
            member_refs=excluded.member_refs, basis=excluded.basis, score=excluded.score,
            detail_json=excluded.detail_json, created_at=excluded.created_at""",
            (decision_id, run_id, dataset_uid, _json(decision["member_refs"]), decision["basis"],
             float(decision["score"]), _json(decision["detail"]), now),
        )
