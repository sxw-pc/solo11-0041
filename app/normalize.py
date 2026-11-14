"""字段归一化：类别、频率、格式、大小、时间与地理范围。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import yaml

# 大小解析来源（与需求约定的三类一致）：括号精确值 > 单位换算 > 无法解析。
SIZE_SOURCE_PARENTHESIZED = "parenthesized_bytes"
SIZE_SOURCE_UNIT = "unit_conversion"
SIZE_SOURCE_UNPARSEABLE = "unparseable"

# 结构化失败 / 提示原因，供字段级 transform 与校验告警使用。
SIZE_REASON_EMPTY = "empty"
SIZE_REASON_NOT_NUMERIC = "not_numeric"
SIZE_REASON_INVALID_NUMBER = "invalid_number"
SIZE_REASON_UNKNOWN_UNIT = "unknown_unit"
SIZE_REASON_AMBIGUOUS = "ambiguous_expression"
SIZE_REASON_CONFLICT = "conflicting_values"
# 无单位、又夹杂在其它文本中的裸数字：不是大小的一部分，扫描时忽略。
_IGNORE_BARE = "bare_number"

_NUMBER_TOKEN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
# 纯数量表达式（可整段匹配），用于括号内容与单值判定。
_QUANTITY_RE = re.compile(rf"({_NUMBER_TOKEN})\s*([A-Za-z]+)?")
# 在混合表达式里扫描数量；边界排除逗号 / 小数点 / 正负号，避免把 12.4.5 之类
# 非法数字截成合法片段，也避免把相邻文本里的数字误当作大小。
_QUANTITY_SCAN_RE = re.compile(
    rf"(?<![A-Za-z0-9_.,+\-])({_NUMBER_TOKEN})(?:\s*([A-Za-z]+))?(?![A-Za-z0-9_.,+\-])"
)
# 空格分隔的千分位（`1 024 KB`）：合并成 `1024 KB` 后再解析，避免把 `024` 当成
# 独立数字；只在“数字 + 空格 + 恰好三位数字”时合并，不会合并普通词间数字。
_SPACE_GROUPING_RE = re.compile(r"(?<=\d)\s(?=\d{3}(?:\D|$))")
_PAREN_RE = re.compile(r"\(([^()]*)\)")


@dataclass(frozen=True)
class SizeParseResult:
    """一次大小解析的结构化结果。

    source 表示字节数的来源；reason 在失败或存在冲突时给出机器可读的原因。
    """

    size_bytes: int | None
    source: str
    reason: str | None = None
    unit: str | None = None

    @property
    def ok(self) -> bool:
        return self.size_bytes is not None

    def canonical(self) -> str:
        """稳定、与写法无关的指纹输入。

        成功时为 `bytes:<n>`，失败时为 `unparseable:<reason>`；因此同一语义的
        `12.4 MB (13002342 bytes)`、`13002342 bytes (12.4 MB)` 等写法得到相同的
        键，空格或顺序差异不会让下游 fingerprint 漂移。
        """
        if self.size_bytes is not None:
            return f"bytes:{self.size_bytes}"
        return f"{SIZE_SOURCE_UNPARSEABLE}:{self.reason or SIZE_REASON_NOT_NUMERIC}"


@dataclass(frozen=True)
class _Quantity:
    size_bytes: int | None
    reason: str | None
    unit: str | None = None
    exact_bytes: bool = False


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


def _unit_multiplier(unit: str, units: dict) -> tuple[int | None, str | None]:
    """按配置查单位换算系数；仅接受 `size_units` 中显式声明的单位。

    十进制与二进制单位如何换算完全由配置决定（例如 `mb: 1048576`），
    解析器不硬编码 1000/1024。
    """
    token = unit.strip().casefold().rstrip(".")
    candidates = [token]
    if token.endswith("s"):
        candidates.append(token[:-1])
    for candidate in candidates:
        if candidate in units:
            try:
                multiplier = int(units[candidate])
            except (TypeError, ValueError):
                return None, None
            if multiplier > 0:
                return multiplier, candidate
    return None, None


def _quantity(expr: str, units: dict, *, require_unit: bool = False) -> _Quantity:
    """解析一个“数字 + 可选单位”片段；不合法时给出结构化原因。

    `require_unit=True` 时，未带单位的裸数字返回内部忽略标记，避免把混合文本
    中的年份等无关数字误当作字节数。
    """
    text = expr.strip()
    match = _QUANTITY_RE.fullmatch(text)
    if not match:
        has_digit = any(char.isdigit() for char in text)
        return _Quantity(None, SIZE_REASON_INVALID_NUMBER if has_digit else SIZE_REASON_NOT_NUMERIC)
    number_text, unit_text = match.group(1), match.group(2)
    if unit_text is None and require_unit:
        return _Quantity(None, _IGNORE_BARE)
    number_text = number_text.replace(",", "")
    try:
        number = Decimal(number_text)
    except InvalidOperation:
        return _Quantity(None, SIZE_REASON_INVALID_NUMBER)
    if not number.is_finite() or number < 0:
        return _Quantity(None, SIZE_REASON_INVALID_NUMBER)
    unit_token = unit_text.casefold() if unit_text else "bytes"
    multiplier, unit_key = _unit_multiplier(unit_token, units)
    if multiplier is None:
        return _Quantity(None, SIZE_REASON_UNKNOWN_UNIT, unit=unit_token)
    try:
        size = int((number * multiplier).to_integral_value(rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return _Quantity(None, SIZE_REASON_INVALID_NUMBER)
    if size > 2**63 - 1:
        return _Quantity(None, SIZE_REASON_INVALID_NUMBER)
    return _Quantity(size, None, unit=unit_key or unit_token, exact_bytes=multiplier == 1)


def _scan_quantities(text: str, units: dict, *, require_unit: bool = True) -> tuple[list[_Quantity], bool]:
    """扫描整段文本中的数量片段，返回解析结果以及是否出现未知单位。"""
    quantities: list[_Quantity] = []
    unknown = False
    for match in _QUANTITY_SCAN_RE.finditer(text):
        item = _quantity(match.group(0), units, require_unit=require_unit)
        if item.reason == _IGNORE_BARE:
            continue
        if item.reason == SIZE_REASON_UNKNOWN_UNIT:
            unknown = True
            continue
        if item.reason is not None:
            continue
        quantities.append(item)
    return quantities, unknown


def _split_quantities(
    chunk: str, units: dict, *, require_unit: bool
) -> tuple[list[int], list[int], list[str], bool]:
    """把一个文本片段拆成 (精确字节值, 换算值, 换算单位, 是否含未知单位)。

    片段若是单一数量表达式则直接解析（保留无单位裸数字的既有语义）；否则扫描
    其中的每个数量，忽略裸数字与非法片段，使 `(12.4 MB / 13002342 bytes)` 这类
    同义写法也能得到一致结果。
    """
    exact: list[int] = []
    converted: list[int] = []
    converted_units: list[str] = []
    if _QUANTITY_RE.fullmatch(chunk.strip()):
        item = _quantity(chunk, units, require_unit=require_unit)
        if item.reason == _IGNORE_BARE or item.reason == SIZE_REASON_NOT_NUMERIC:
            return exact, converted, converted_units, False
        if item.reason == SIZE_REASON_UNKNOWN_UNIT:
            return exact, converted, converted_units, True
        if item.reason is not None:
            return exact, converted, converted_units, False
        if item.exact_bytes:
            exact.append(item.size_bytes)
        else:
            converted.append(item.size_bytes)
            converted_units.append(item.unit or "")
        return exact, converted, converted_units, False

    quantities, unknown = _scan_quantities(chunk, units, require_unit=require_unit)
    for item in quantities:
        if item.exact_bytes:
            exact.append(item.size_bytes)
        else:
            converted.append(item.size_bytes)
            converted_units.append(item.unit or "")
    return exact, converted, converted_units, unknown


def _unable(reason: str | None, unit: str | None = None) -> SizeParseResult:
    return SizeParseResult(None, SIZE_SOURCE_UNPARSEABLE, reason or SIZE_REASON_NOT_NUMERIC, unit)


def parse_size_details(raw: object, units: dict | None) -> SizeParseResult:
    """把大小表达式解析为字节数，并报告来源与失败原因。

    明确优先级（与书写顺序无关）：

    1. 括号内精确值：括号中的纯字节数量（`bytes`/`b`，换算系数 1，如
       `12.4 MB (13002342 bytes)` 里的 `13002342 bytes`）是权威来源，标记为
       `parenthesized_bytes`。
    2. 单位换算：其余数量按 `units` 配置换算（如 `12.4 MB`），标记为
       `unit_conversion`。十进制 / 二进制系数完全取自配置，不做硬编码。
    3. 无法解析：未知单位返回 `unknown_unit`，非法数字返回 `invalid_number`，
       纯文本返回 `not_numeric`。

    若精确值与换算值互相矛盾（四舍五入到字节后仍不相等），或出现多个互相矛盾的
    精确值，不猜测任一来源，统一返回 `conflicting_values`；多个换算值不一致则
    返回 `ambiguous_expression`。这样不会输出自相矛盾的 `size_bytes`。

    括号内外顺序、空白与千分位逗号都不影响 `size_bytes`，同一 `size_raw` 反复
    归一化得到完全一致的结果。
    """
    unit_map = {str(key).casefold(): value for key, value in (units or {}).items()}
    text = unicodedata.normalize("NFKC", _text(raw))
    if not text:
        return _unable(SIZE_REASON_EMPTY)
    # 归一化空白分隔的千分位，使 `1 024 KB` 与 `1,024 KB` / `1024 KB` 等价。
    text = _SPACE_GROUPING_RE.sub("", text)

    # 单值表达式：保留“无单位数字默认按字节”的既有语义。纯字节值也经由
    # `size_units` 查表（换算系数 1），因此来源统一记为 `unit_conversion`。
    if _QUANTITY_RE.fullmatch(text):
        item = _quantity(text, unit_map)
        if item.reason is not None:
            return _unable(item.reason, item.unit)
        return SizeParseResult(item.size_bytes, SIZE_SOURCE_UNIT, None, item.unit)

    exact_values: list[int] = []
    exact_in_parens = False
    converted_values: list[int] = []
    converted_units: list[str] = []
    unknown_unit = False

    # 括号内片段：带字节单位的数量构成精确值，其余带单位的进入换算候选；
    # 括号内的裸数字（如“(2 files)”）不作为大小，避免误判。
    for group in _PAREN_RE.findall(text):
        if not group.strip():
            continue
        exact, converted, units_seen, unknown = _split_quantities(
            group, unit_map, require_unit=True
        )
        if exact:
            exact_values.extend(exact)
            exact_in_parens = True
        converted_values.extend(converted)
        converted_units.extend(units_seen)
        unknown_unit = unknown_unit or unknown

    # 括号外文本（剔除括号部分，避免重复计数）单独扫描。
    exact, converted, units_seen, unknown = _split_quantities(
        _PAREN_RE.sub(" ", text), unit_map, require_unit=True
    )
    exact_values.extend(exact)
    converted_values.extend(converted)
    converted_units.extend(units_seen)
    unknown_unit = unknown_unit or unknown

    # 优先级 1：精确字节数。精确值之间、精确值与换算值之间的矛盾都显式报告。
    if exact_values:
        anchors = set(exact_values)
        if len(anchors) > 1:
            return _unable(SIZE_REASON_CONFLICT, "bytes")
        anchor = anchors.pop()
        if any(value != anchor for value in converted_values):
            return _unable(SIZE_REASON_CONFLICT, "bytes")
        # 三类来源约定：只有括号内精确值单独记为一类，其余成功的字节数都视为
        # 经由配置单位表换算得到。
        source = SIZE_SOURCE_PARENTHESIZED if exact_in_parens else SIZE_SOURCE_UNIT
        return SizeParseResult(anchor, source, None, "bytes")

    # 优先级 2：单位换算（换算系数完全来自配置）。
    if converted_values:
        values = set(converted_values)
        if len(values) > 1:
            return _unable(SIZE_REASON_AMBIGUOUS, converted_units[0] if converted_units else None)
        return SizeParseResult(values.pop(), SIZE_SOURCE_UNIT, None, converted_units[0] or None)

    # 优先级 3：无法解析，区分未知单位与非法数字。
    if unknown_unit:
        return _unable(SIZE_REASON_UNKNOWN_UNIT)
    if any(char.isdigit() for char in text):
        return _unable(SIZE_REASON_INVALID_NUMBER)
    return _unable(SIZE_REASON_NOT_NUMERIC)


def parse_size(raw: object, units: dict) -> int | None:
    """将 12.4 MB / 18,432,000 bytes 统一为字节数；非法值返回 None。

    需要区分失败原因时改用 `parse_size_details`。
    """
    return parse_size_details(raw, units).size_bytes


def resource_fingerprint_view(resource: dict) -> dict:
    """构造稳定的资源指纹视图：用 `size_key` 替换原始大小文本。

    这样 `12.4 MB (13002342 bytes)`、`13002342 bytes (12.4 MB)`、`12.4MB(13,002,342
    bytes)` 等语义相同的写法得到相同指纹，空格和书写顺序不会影响幂等计数。
    未经过 `normalize_record` 的资源原样返回，保持向后兼容。
    """
    view = dict(resource)
    size_key = view.get("size_key")
    if size_key is not None:
        view["size_raw"] = size_key
    view.pop("size_key", None)
    return view


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
        parsed = parse_size_details(raw_size, units)
        size_bytes = parsed.size_bytes
        if raw_size:
            status = "applied" if parsed.ok else "warn"
            transform = _transform(
                f"resources[{index}].size", "resource.size", raw_size, size_bytes, status
            )
            # 字段级 transform 记录本次大小的解析来源（括号精确值 / 单位换算），
            # 无法解析时给出结构化原因（未知单位、非法数字等）。
            transform["size_source"] = parsed.source
            transform["size_reason"] = parsed.reason
            transform["size_unit"] = parsed.unit
            transforms.append(transform)
        normalized_resources.append(
            {
                **resource,
                "format": fmt,
                "size_bytes": size_bytes,
                # 与写法无关的解析键，供指纹使用；空格 / 顺序 / 千分位差异不会
                # 让同一语义的大小产生不同的 fingerprint。
                "size_key": parsed.canonical(),
            }
        )
    result["resources"] = normalized_resources
    result["transforms"] = transforms
    return result, transforms
