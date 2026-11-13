"""字段级校验与告警分级；告警不阻断入库。"""

from __future__ import annotations

from typing import Iterable

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def _evidence(value: object, limit: int = 240) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:limit]


def _finding(
    severity: str,
    rule_id: str,
    field_name: str,
    evidence: str,
    instance_id: str | None = None,
) -> dict:
    return {
        "severity": severity,
        "rule_id": rule_id,
        "field_name": field_name,
        "evidence": _evidence(evidence),
        "instance_id": instance_id,
    }


def _is_missing(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def validate_record(record: dict, rules: dict) -> list[dict]:
    """校验一条已归一化实例，返回绑定实例的告警列表。"""
    settings = rules.get("validation") or {}
    findings: list[dict] = []
    instance_id = record.get("instance_id")
    for name in settings.get("required") or []:
        if _is_missing(record.get(name)):
            findings.append(
                _finding("error", "required.missing", name, f"required field '{name}' is absent", instance_id)
            )

    allowed_categories = set(settings.get("allowed_categories") or [])
    category = record.get("category")
    if category and allowed_categories and category not in allowed_categories:
        findings.append(
            _finding("error", "enum.category", "category", f"category '{category}' is outside enum", instance_id)
        )
    allowed_frequencies = set(settings.get("allowed_frequencies") or [])
    frequency = record.get("update_frequency")
    if frequency and allowed_frequencies and frequency not in allowed_frequencies:
        findings.append(
            _finding(
                "error",
                "enum.update_frequency",
                "update_frequency",
                f"frequency '{frequency}' is outside enum",
                instance_id,
            )
        )

    start, end = record.get("temporal_start"), record.get("temporal_end")
    if start and end and start > end:
        findings.append(
            _finding("error", "temporal.start_after_end", "temporal", f"start={start} is after end={end}", instance_id)
        )

    allowed_types = set((rules.get("field_types") or {}).get("canonical") or [])
    for index, field in enumerate(record.get("fields") or [], start=1):
        field_type = field.get("type")
        if allowed_types and field_type not in allowed_types:
            findings.append(
                _finding(
                    "warn",
                    "field.type_unknown",
                    f"fields[{index}].type",
                    f"field '{field.get('name')}' has unknown type '{field_type}'",
                    instance_id,
                )
            )

    for index, resource in enumerate(record.get("resources") or [], start=1):
        raw_size = _evidence(resource.get("size_raw"))
        if raw_size and resource.get("size_bytes") is None:
            findings.append(
                _finding(
                    "warn",
                    "resource.size_not_numeric",
                    f"resources[{index}].size",
                    f"size value '{raw_size}' cannot be converted to bytes",
                    instance_id,
                )
            )
        if _is_missing(resource.get("url")):
            findings.append(
                _finding(
                    "warn",
                    "resource.url_missing",
                    f"resources[{index}].url",
                    "resource link is absent",
                    instance_id,
                )
            )

    extraction = record.get("extraction") or {}
    if extraction.get("fallback_used"):
        field_names = [name for name, trace in (extraction.get("fields") or {}).items() if trace.get("fallback")]
        lists = [name for name, trace in (extraction.get("lists") or {}).items() if trace.get("fallback")]
        evidence = "fallback used: " + ", ".join(field_names + [f"{name}_list" for name in lists])
        findings.append(
            _finding("warn", "extraction.fallback_used", "extraction", evidence, instance_id)
        )
    return findings


def validate_cross_source(instances: Iterable[dict], rules: dict) -> list[dict]:
    """仅在跨源合并组内比较字段数，差异过大时产生 info 级告警。"""
    items = list(instances)
    sources = {item.get("source") for item in items}
    if len(sources) < 2:
        return []
    counts = {item.get("source"): len(item.get("fields") or []) for item in items}
    high = max(counts.values(), default=0)
    low = min(counts.values(), default=0)
    gap = high - low
    policy = (rules.get("validation") or {}).get("cross_source_field_gap") or {}
    minimum = int(policy.get("absolute", 3))
    ratio = float(policy.get("ratio", 0.5))
    if high == 0 or gap < minimum or (gap / high) < ratio:
        return []
    detail = ", ".join(f"{source}={count}" for source, count in sorted(counts.items()))
    return [
        _finding(
            "info",
            "cross_source.field_count_gap",
            "fields",
            f"cross-source field count gap {gap}: {detail}",
            "__cross_source__",
        )
    ]


def summarize(findings: Iterable[dict]) -> dict:
    summary = {"error": 0, "warn": 0, "info": 0, "total": 0}
    for finding in findings:
        severity = str(finding.get("severity", "info"))
        if severity not in summary:
            severity = "info"
        summary[severity] += 1
        summary["total"] += 1
    return summary


def sort_findings(findings: Iterable[dict]) -> list[dict]:
    return sorted(
        findings,
        key=lambda item: (
            SEVERITY_ORDER.get(str(item.get("severity")), 9),
            str(item.get("dataset_uid", "")),
            str(item.get("field_name", "")),
            str(item.get("rule_id", "")),
        ),
    )
