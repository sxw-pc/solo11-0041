"""字段归一化：类别、频率、格式、大小、时间与地理范围。"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

import yaml


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canon(value: object) -> str:
    """用于同义词查表的宽松键：大小写、斜杠、& 与连字符不影响匹配。"""
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    text = re.sub(r"[/&_\-]+", " ", text)
    return re.sub(r"[^\w一-鿿]+", " ", text).strip()


def normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    text = re.sub(r"[^\w一-鿿]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _synonyms(groups: dict) -> dict[str, str]:
    index: dict[str, str] = {}
    for canonical, aliases in groups.items():
        for alias in [canonical, *aliases]:
            index[_canon(alias)] = canonical
    return index


def _transform(field: str, rule: str, before: object, after: object, status: str = "applied") -> dict:
    return {
        "field_name": field,
        "rule": rule,
        "before_value": None if before is None else str(before),
        "after_value": None if after is None else str(after),
        "status": status,
    }


def parse_size(raw: object, units: dict) -> int | None:
    """将 12.4 MB / 18,432,000 bytes 统一为字节数；非法值返回 None。"""
    text = _text(raw).casefold().replace(",", "")
    if not text:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-z]+)?", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "bytes").casefold()
    multiplier = units.get(unit)
    if multiplier is None:
        return None
    return int(round(number * int(multiplier)))


def _date_token(token: str, end: bool) -> str | None:
    token = token.strip()
    try:
        if re.fullmatch(r"\d{4}", token):
            year = int(token)
            return date(year, 12 if end else 1, 31 if end else 1).isoformat()
        if re.fullmatch(r"\d{4}-\d{1,2}", token):
            year, month = (int(part) for part in token.split("-"))
            if end:
                next_month = date(year + (month == 12), month % 12 + 1, 1)
                return date.fromordinal(next_month.toordinal() - 1).isoformat()
            return date(year, month, 1).isoformat()
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", token):
            return date.fromisoformat(token).isoformat()
    except ValueError:
        return None
    return None


def parse_temporal(raw: object) -> tuple[str | None, str | None, str]:
    """返回 (ISO 开始, ISO 结束, status)。单年份补全整年，年份区间补全首尾。"""
    text = _text(raw)
    if not text:
        return None, None, "missing"
    token = r"\d{4}(?:-\d{1,2}(?:-\d{1,2})?)?"
    match = re.fullmatch(rf"\s*({token})\s*(?:/|to|through|until|–|—|-)\s*({token})\s*", text, re.IGNORECASE)
    if match:
        start = _date_token(match.group(1), False)
        end = _date_token(match.group(2), True)
        return start, end, "applied" if start and end else "warn"
    single = _date_token(text, False)
    if single:
        return single, _date_token(text, True), "applied"
    return None, None, "warn"


def _normalize_category(raw: object, index: dict, status: str = "applied") -> tuple[str | None, dict | None]:
    value = _text(raw)
    if not value:
        return None, None
    mapped = index.get(_canon(value))
    if mapped:
        state = "unchanged" if mapped == value else status
        return mapped, _transform("category", "category.synonym", value, mapped, state)
    fallback = _canon(value).replace(" ", "-")
    return fallback, _transform("category", "category.synonym", value, fallback, "warn")


def _normalize_frequency(raw: object, index: dict) -> tuple[str | None, dict | None]:
    value = _text(raw)
    if not value:
        return None, None
    mapped = index.get(_canon(value))
    if mapped:
        state = "unchanged" if mapped == value else "applied"
        return mapped, _transform("update_frequency", "frequency.enum", value, mapped, state)
    return "unknown", _transform("update_frequency", "frequency.enum", value, "unknown", "warn")


def _normalize_geography(raw: object, index: dict) -> tuple[str | None, dict | None]:
    value = _text(raw)
    if not value:
        return None, None
    mapped = index.get(_canon(value))
    if mapped:
        state = "unchanged" if mapped == value else "applied"
        return mapped, _transform("geography", "geography.code", value, mapped, state)
    fallback = re.sub(r"\s+", "-", value.upper())
    return fallback, _transform("geography", "geography.code", value, fallback, "warn")


def _normalize_format(raw: object, canonical: set[str], aliases: dict) -> tuple[str, dict]:
    value = _text(raw)
    upper = value.upper()
    mapped = upper if upper in canonical else str(aliases.get(value.casefold()) or upper)
    mapped = mapped.upper()
    status = "unchanged" if mapped == value else "applied"
    if mapped not in canonical and mapped != "OTHER":
        status = "warn"
    return mapped or "OTHER", _transform("format", "format.canonical", value, mapped or "OTHER", status)


def _normalize_field_type(raw: object, canonical: set[str], aliases: dict) -> tuple[str, dict]:
    before = _text(raw)
    value = before.casefold()
    mapped = value if value in canonical else str(aliases.get(value) or value)
    status = "unchanged" if mapped == before else "applied"
    if mapped not in canonical:
        status = "warn"
    return mapped, _transform("field.type", "field_type.canonical", before, mapped, status)


LINEAGE_FIELDS = ("category", "update_frequency", "geography")


def field_normalization_status(transforms: list[dict], normalized: dict) -> dict[str, dict]:
    """汇总 category / update_frequency / geography 的归一化状态，供跨源合并追踪降级谱系。

    词表字段的 transform 各只有一条；字段在来源中缺失（无 transform）时记为 missing。
    """
    status: dict[str, dict] = {}
    by_field: dict[str, dict] = {}
    for transform in transforms:
        name = transform.get("field_name")
        if name in LINEAGE_FIELDS and name not in by_field:
            by_field[name] = transform
    for name in LINEAGE_FIELDS:
        transform = by_field.get(name)
        if transform is None:
            status[name] = {
                "rule": None,
                "status": "missing",
                "before_value": None,
                "after_value": normalized.get(name),
            }
        else:
            status[name] = {
                "rule": transform.get("rule"),
                "status": transform.get("status"),
                "before_value": transform.get("before_value"),
                "after_value": transform.get("after_value"),
            }
    return status


def load_rules(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def normalize_record(record: dict, rules: dict) -> tuple[dict, list[dict]]:
    """归一化单条来源实例，返回新记录和字段级审计轨迹。"""
    transforms: list[dict] = []
    category_index = _synonyms(rules.get("categories") or {})
    frequency_index = _synonyms(rules.get("frequency") or {})
    geography_index = _synonyms(rules.get("geography") or {})
    license_index = _synonyms(rules.get("license_aliases") or {})
    format_cfg = rules.get("formats") or {}
    formats = set(format_cfg.get("canonical") or [])
    format_aliases = {str(k).casefold(): v for k, v in (format_cfg.get("aliases") or {}).items()}
    type_cfg = rules.get("field_types") or {}
    field_types = set(type_cfg.get("canonical") or [])
    type_aliases = {str(k).casefold(): v for k, v in (type_cfg.get("aliases") or {}).items()}
    units = {str(k).casefold(): v for k, v in (rules.get("size_units") or {}).items()}

    result = dict(record)
    result["title"] = _text(record.get("title"))
    result["title_norm"] = normalize_title(result["title"])
    result["publisher"] = _text(record.get("publisher"))
    result["publisher_norm"] = normalize_title(result["publisher"])

    category, transform = _normalize_category(record.get("category"), category_index)
    result["category"] = category
    if transform:
        transforms.append(transform)
    frequency, transform = _normalize_frequency(record.get("update_frequency"), frequency_index)
    result["update_frequency"] = frequency
    if transform:
        transforms.append(transform)
    geography, transform = _normalize_geography(record.get("geography"), geography_index)
    result["geography"] = geography
    if transform:
        transforms.append(transform)

    raw_license = _text(record.get("license"))
    canonical_license = license_index.get(_canon(raw_license))
    result["license"] = canonical_license or (raw_license or None)
    if raw_license:
        state = "unchanged" if result["license"] == raw_license else "applied"
        transforms.append(_transform("license", "license.alias", raw_license, result["license"], state))

    start, end, temporal_status = parse_temporal(record.get("temporal"))
    result["temporal_start"] = start
    result["temporal_end"] = end
    if _text(record.get("temporal")):
        after = f"{start}/{end}" if start and end else None
        transforms.append(_transform("temporal", "temporal.iso_range", record.get("temporal"), after, temporal_status))

    normalized_fields: list[dict] = []
    for index, field in enumerate(record.get("fields") or [], start=1):
        field_type, type_transform = _normalize_field_type(field.get("type"), field_types, type_aliases)
        type_transform["field_name"] = f"fields[{index}].type"
        transforms.append(type_transform)
        normalized_fields.append({**field, "type": field_type})
    result["fields"] = normalized_fields

    normalized_resources: list[dict] = []
    for index, resource in enumerate(record.get("resources") or [], start=1):
        fmt, format_transform = _normalize_format(resource.get("format"), formats, format_aliases)
        format_transform["field_name"] = f"resources[{index}].format"
        transforms.append(format_transform)
        raw_size = _text(resource.get("size_raw"))
        size_bytes = parse_size(raw_size, units)
        if raw_size:
            status = "applied" if size_bytes is not None else "warn"
            transforms.append(
                _transform(f"resources[{index}].size", "resource.size", raw_size, size_bytes, status)
            )
        normalized_resources.append({**resource, "format": fmt, "size_bytes": size_bytes})
    result["resources"] = normalized_resources
    result["transforms"] = transforms
    result["normalization_status"] = field_normalization_status(transforms, result)
    return result, transforms
