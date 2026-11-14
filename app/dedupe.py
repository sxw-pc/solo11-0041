"""跨源数据集归并：标题相似度 + 发布方 / 时间范围指纹。"""

from __future__ import annotations

import hashlib
import json
from difflib import SequenceMatcher

from .normalize import LINEAGE_FIELDS, normalize_title

# 命中词表规范值的归一化状态；warn 为兜底降级，missing 为来源缺失。
_CANONICAL_STATUSES = {"applied", "unchanged"}


def content_fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def instance_id(source: str, source_url: str) -> str:
    digest = hashlib.sha1(f"{source}|{source_url}".encode("utf-8")).hexdigest()
    return "inst_" + digest[:16]


def _source_rank(source: str, priority: list[str]) -> int:
    try:
        return priority.index(source)
    except ValueError:
        return len(priority) + 1


def _ordered(instances: list[dict], config: dict) -> list[dict]:
    priority = list(config.get("field_priority") or [])
    return sorted(instances, key=lambda item: (_source_rank(item.get("source", ""), priority), item.get("source_url", "")))


def _matching_score(left: dict, right: dict, config: dict) -> tuple[float, str] | None:
    if not left.get("source") or not right.get("source") or left.get("source") == right.get("source"):
        return None
    publisher_left = left.get("publisher_norm") or normalize_title(left.get("publisher"))
    publisher_right = right.get("publisher_norm") or normalize_title(right.get("publisher"))
    if config.get("require_same_publisher", True):
        if not publisher_left or publisher_left != publisher_right:
            return None
    if config.get("require_same_period", True):
        if (left.get("temporal_start"), left.get("temporal_end")) != (
            right.get("temporal_start"),
            right.get("temporal_end"),
        ):
            return None
        if not left.get("temporal_start") or not left.get("temporal_end"):
            return None
    if config.get("require_same_geography", True):
        if not left.get("geography") or left.get("geography") != right.get("geography"):
            return None
    title_left = left.get("title_norm") or normalize_title(left.get("title"))
    title_right = right.get("title_norm") or normalize_title(right.get("title"))
    if not title_left or not title_right:
        return None
    if title_left == title_right:
        score = 1.0
    else:
        score = SequenceMatcher(None, title_left, title_right).ratio()
    threshold = float(config.get("title_similarity_threshold", 0.86))
    if score < threshold:
        return None
    return round(score, 4), "exact_title+publisher+period" if score == 1.0 else "fuzzy_title+publisher+period"


def _first(instances: list[dict], key: str):
    for item in instances:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _field_lineage(instance: dict) -> dict[str, dict]:
    """单个成员在 category / update_frequency / geography 上的归一化谱系。

    优先使用 normalize_record 透传的 normalization_status；缺失时（如测试直接构造的
    实例）回退到 transforms 审计轨迹，再缺失则按来源值是否为空记 missing。
    """
    status_map = instance.get("normalization_status")
    if not isinstance(status_map, dict):
        status_map = {}
    transforms = {
        item.get("field_name"): item
        for item in instance.get("transforms") or []
        if item.get("field_name") in LINEAGE_FIELDS
    }
    lineage: dict[str, dict] = {}
    for field in LINEAGE_FIELDS:
        entry = status_map.get(field)
        if not isinstance(entry, dict):
            transform = transforms.get(field)
            if transform is not None:
                entry = {
                    "rule": transform.get("rule"),
                    "status": transform.get("status"),
                    "before_value": transform.get("before_value"),
                    "after_value": transform.get("after_value"),
                }
            else:
                value = instance.get(field)
                entry = {
                    "rule": None,
                    "status": "missing" if value in (None, "", [], {}) else "unchanged",
                    "before_value": None,
                    "after_value": value,
                }
        lineage[field] = {
            "rule": entry.get("rule"),
            "status": entry.get("status"),
            "raw_value": entry.get("before_value"),
            "normalized_value": entry.get("after_value"),
        }
    return lineage


def _inherited_degraded_fields(lineage_by_instance: list[dict[str, dict]]) -> list[str]:
    """主来源取到规范值、而其他成员该字段为 warn 降级时，标记为继承降级。

    主来源自身即 warn/missing 时不标记：那不是从别处继承来的降级。
    入参为各成员经 _field_lineage 计算后的字段谱系（按 field_priority 排序）。
    """
    primary_lineage = lineage_by_instance[0]
    marked: list[str] = []
    for field in LINEAGE_FIELDS:
        if primary_lineage[field]["status"] not in _CANONICAL_STATUSES:
            continue
        if any(lineage[field]["status"] == "warn" for lineage in lineage_by_instance[1:]):
            marked.append(field)
    return marked


def _make_group(members: list[dict], score: float, basis: str, config: dict) -> dict:
    instances = _ordered(members, config)
    primary = instances[0]
    title = _first(instances, "title") or ""
    publisher = _first(instances, "publisher") or ""
    lineage_by_instance = [_field_lineage(item) for item in instances]
    member_lineage = [
        {
            "instance_id": item.get("instance_id"),
            "source": item.get("source"),
            "url": item.get("source_url"),
            "is_primary": item is primary,
            "fields": lineage_by_instance[index],
        }
        for index, item in enumerate(instances)
    ]
    inherited = _inherited_degraded_fields(lineage_by_instance)
    group = {
        "title": title,
        "title_norm": normalize_title(title),
        "category": _first(instances, "category"),
        "publisher": publisher,
        "publisher_norm": normalize_title(publisher),
        "temporal_start": _first(instances, "temporal_start"),
        "temporal_end": _first(instances, "temporal_end"),
        "geography": _first(instances, "geography"),
        "license": _first(instances, "license"),
        "update_frequency": _first(instances, "update_frequency"),
        "fields": list(_first(instances, "fields") or []),
        "resources": list(_first(instances, "resources") or []),
        "instances": instances,
        "source_count": len({item.get("source") for item in instances}),
        "score": score,
        "basis": basis,
        "primary_source": primary.get("source"),
        "inherited_degraded_fields": inherited,
        "merge_members": [
            {
                "instance_id": item.get("instance_id"),
                "source": item.get("source"),
                "url": item.get("source_url"),
            }
            for item in instances
        ],
    }
    key = "|".join(
        [
            group["title_norm"],
            group["publisher_norm"],
            group["temporal_start"] or "",
            group["temporal_end"] or "",
        ]
    )
    if not group["publisher_norm"]:
        key += f"|{primary.get('source')}|{primary.get('source_url')}"
    group["dataset_uid"] = "ds_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    fingerprint_payload = {
        "key": key,
        "category": group["category"],
        "geography": group["geography"],
        "license": group["license"],
        "update_frequency": group["update_frequency"],
        "fields": group["fields"],
        "resources": group["resources"],
        "instances": sorted(item.get("instance_id", "") for item in instances),
        "sources": sorted({item.get("source", "") for item in instances}),
    }
    group["fingerprint"] = content_fingerprint(fingerprint_payload)
    group["merge_decision"] = {
        "member_refs": group["merge_members"],
        "basis": basis,
        "score": score,
        "manual_override": 0,
        "detail": {
            "title_norm": group["title_norm"],
            "source_count": group["source_count"],
            "primary_source": group["primary_source"],
            "inherited_degraded_fields": inherited,
            "member_lineage": member_lineage,
        },
    }
    return group


def group_instances(instances: list[dict], config: dict) -> list[dict]:
    """仅跨源归并；以主来源（field_priority 越小越优先）代表整个簇。"""
    ordered = _ordered(instances, config)
    clusters: list[dict] = []
    for item in ordered:
        placed = False
        for cluster in clusters:
            match = _matching_score(cluster["primary"], item, config)
            if match:
                score, basis = match
                cluster["members"].append(item)
                cluster["score"] = max(cluster["score"], score)
                if cluster["basis"] in {"singleton", "exact_title+publisher+period"} and basis.startswith("fuzzy"):
                    cluster["basis"] = basis
                elif cluster["basis"] == "singleton":
                    cluster["basis"] = basis
                placed = True
                break
        if not placed:
            clusters.append({"primary": item, "members": [item], "score": 0.0, "basis": "singleton"})
    groups: list[dict] = []
    for cluster in clusters:
        members = _ordered(cluster["members"], config)
        basis = cluster["basis"]
        if len(members) > 1 and basis == "singleton":
            basis = "exact_title+publisher+period"
        group = _make_group(members, cluster["score"] if len(members) > 1 else 1.0, basis, config)
        groups.append(group)
    used: set[str] = set()
    for group in groups:
        if group["dataset_uid"] in used:
            # 同源重复目录项不跨源合并，但仍需分配不冲突且稳定的 UID。
            salt = group["instances"][0].get("instance_id", "")
            group["dataset_uid"] = "ds_" + hashlib.sha1(
                f"{group['dataset_uid']}|{salt}".encode("utf-8")
            ).hexdigest()[:16]
        used.add(group["dataset_uid"])
    return groups
