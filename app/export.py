"""将谱系档案导出为 JSON、Markdown 与统计快照。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .repository import snapshot

SEVERITY_LABELS = {"error": "错误", "warn": "告警", "info": "信息"}


def _md(value: object) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _filesize(resource: dict) -> str:
    size = resource.get("size_bytes")
    raw = resource.get("size_raw") or "unknown"
    return f"{raw} ({size} bytes)" if size is not None else str(raw)


def _finding_line(finding: dict) -> str:
    instance = finding.get("instance_id") or "dataset"
    return (
        f"- **{finding.get('severity', 'info').upper()}** "
        f"`{finding.get('rule_id')}` · `{finding.get('field_name')}` · {instance} — "
        f"{finding.get('evidence', '')}"
    )


def _render_dataset(item: dict) -> list[str]:
    sources = sorted({inst.get("source") for inst in item.get("instances", []) if inst.get("source")})
    lines = [
        f"### {_md(item.get('title'))}",
        "",
        f"- `dataset_uid`: `{item.get('dataset_uid')}`",
        f"- 类别 / 发布方: **{_md(item.get('category'))}** / {_md(item.get('publisher'))}",
        f"- 时间范围: {_md(item.get('temporal_start'))} → {_md(item.get('temporal_end'))}",
        f"- 地理范围: {_md(item.get('geography'))} · 许可: {_md(item.get('license'))}",
        f"- 更新频率: {_md(item.get('update_frequency'))} · 来源: {', '.join(sources) or '—'}",
        "",
    ]
    lines.append(f"**字段（{len(item.get('fields', []))}）**")
    for field in item.get("fields", []):
        lines.append(
            f"- `{_md(field.get('name'))}` ({_md(field.get('type'))}): {_md(field.get('description'))}"
        )
    if not item.get("fields"):
        lines.append("- —")
    lines += ["", f"**资源（{len(item.get('resources', []))}）**"]
    for resource in item.get("resources", []):
        lines.append(
            f"- {_md(resource.get('format'))} · {_md(_filesize(resource))} · `{_md(resource.get('url'))}`"
        )
    if not item.get("resources"):
        lines.append("- —")
    lines += ["", "**来源溯源**"]
    for provenance in item.get("provenance", []):
        raw = provenance.get("raw_archive") or {}
        raw_text = f"{raw.get('file_path')} · sha256 {raw.get('sha256')}" if raw else "未找到原始留档"
        lines.append(f"- `{provenance.get('source')}` — {provenance.get('url')}  ")
        lines.append(f"  留档: {raw_text}")
    findings = item.get("validation_findings", [])
    if findings:
        lines += ["", f"**告警（{len(findings)}）**"]
        lines.extend(_finding_line(finding) for finding in findings)
    decision = item.get("merge_decision")
    if decision:
        lines += [
            "",
            f"**跨源合并**: {decision.get('basis')} · score={decision.get('score')} · "
            f"manual_override={decision.get('manual_override')}",
        ]
        for member in decision.get("member_refs", []):
            lines.append(f"- {member.get('source')} · {member.get('url')}")
    lines += ["", "---", ""]
    return lines


def render_markdown(archive: dict) -> str:
    stats = archive["stats"]
    lines = [
        "# 数据集谱系档案",
        "",
        f"> 生成时间：{archive['generated_at']} · run_id：{_md(archive.get('run_id'))}",
        "",
        "## 概览",
        "",
        "| 指标 | 数量 |",
        "| --- | ---: |",
        f"| 数据集 | {stats['dataset_count']} |",
        f"| 来源实例 | {stats['instance_count']} |",
        f"| 合并数据集 | {stats['merged_dataset_count']} |",
        f"| 字段 | {stats['field_count']} |",
        f"| 资源 | {stats['resource_count']} |",
        f"| 原始留档页面 | {stats['raw_archive_count']} |",
        "",
        "## 告警摘要",
        "",
        "| 级别 | 数量 |",
        "| --- | ---: |",
    ]
    for severity in ("error", "warn", "info"):
        lines.append(f"| {SEVERITY_LABELS[severity]} | {stats['finding_counts'].get(severity, 0)} |")
    all_findings = [finding for item in archive["datasets"] for finding in item.get("validation_findings", [])]
    if all_findings:
        rule_counts = Counter(finding.get("rule_id") for finding in all_findings)
        lines += ["", "按规则：" + " · ".join(f"`{rule}`×{count}" for rule, count in rule_counts.most_common())]
    lines.append("")
    grouped: dict[str, list[dict]] = {}
    for item in archive["datasets"]:
        grouped.setdefault(item.get("category") or "unknown", []).append(item)
    for category in sorted(grouped):
        lines += [f"## {category}（{len(grouped[category])}）", ""]
        for item in grouped[category]:
            lines.extend(_render_dataset(item))
    if not archive["datasets"]:
        lines += ["_档案为空。_", ""]
    return "\n".join(lines)


def export_archive(db_path: str, output_dir: str | Path, run_id: str | None = None) -> dict:
    archive = snapshot(db_path, run_id)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "archive.json"
    md_path = target / "archive.md"
    stats_path = target / "stats.json"
    json_path.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(archive), encoding="utf-8")
    stats_path.write_text(json.dumps(archive["stats"], ensure_ascii=False, indent=2), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path), "stats": str(stats_path), "archive": archive}


def export_one(db_path: str, fmt: str, output_dir: str | Path, run_id: str | None = None) -> tuple[str, str]:
    """供本地控制 API 按格式即时导出，返回 (MIME, 文本内容)。"""
    result = export_archive(db_path, output_dir, run_id)
    if fmt.lower() == "md":
        return "text/markdown; charset=utf-8", Path(result["md"]).read_text(encoding="utf-8")
    return "application/json; charset=utf-8", json.dumps(result["archive"], ensure_ascii=False, indent=2)
