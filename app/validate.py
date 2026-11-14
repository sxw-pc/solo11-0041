"""字段级校验与告警分级；告警不阻断入库。"""

from __future__ import annotations

from typing import Iterable

from .normalize import (
    SIZE_REASON_AMBIGUOUS,
    SIZE_REASON_CONFLICT,
    SIZE_REASON_INVALID_NUMBER,
    SIZE_REASON_UNKNOWN_UNIT,
    parse_size_details,
)

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}

# 大小解析失败原因到校验规则 ID 的映射；未知单位与非法数字各有独立规则，
# 便于告警消费方区分“单位不认识”和“数字本身不合法”。
_SIZE_RULE_IDS = {
    SIZE_REASON_UNKNOWN_UNIT: "resource.size_unknown_unit",
    SIZE_REASON_INVALID_NUMBER: "resource.size_invalid_number",
    SIZE_REASON_AMBIGUOUS: "resource.size_ambiguous",
    SIZE_REASON_CONFLICT: "resource.size_conflict",
}
DEFAULT_SIZE_RULE_ID = "resource.size_not_numeric"


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


def _size_finding(index: int, raw_size: str, rules: dict, instance_id: str | None) -> dict:
    """按解析失败原因生成字段级告警，把“未知单位”和“非法数字”区分开。

    校验阶段用与归一化完全相同的 `size_units` 配置重新解析一次，因此即使调用方
    直接构造 record（未经 `normalize_record`），也能得到一致的结构化原因。
    """
    parsed = parse_size_details(raw_size, (rules or {}).get("size_units") or {})
    reason, source = parsed.reason, parsed.source
    rule_id = _SIZE_RULE_IDS.get(str(reason), DEFAULT_SIZE_RULE_ID)
    field_name = f"resources[{index}].size"
    if reason == SIZE_REASON_UNKNOWN_UNIT:
        detail = f" (unit '{parsed.unit}')" if parsed.unit else ""
        evidence = f"size value '{raw_size}' uses an unknown unit{detail}"
    elif reason == SIZE_REASON_INVALID_NUMBER:
        evidence = f"size value '{raw_size}' has an invalid number"
    elif reason == SIZE_REASON_AMBIGUOUS:
        evidence = f"size value '{raw_size}' contains multiple conflicting quantities"
    elif reason == SIZE_REASON_CONFLICT:
        evidence = f"size value '{raw_size}' has inconsistent exact and converted values"
    else:
        evidence = f"size value '{raw_size}' cannot be converted to bytes"
    finding = _finding("warn", rule_id, field_name, evidence, instance_id)
    finding["size_reason"] = reason
    finding["size_source"] = source
    return finding


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
            findings.append(_size_finding(index, raw_size, rules, instance_id))
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
