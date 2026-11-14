from app.normalize import (
    SIZE_REASON_AMBIGUOUS,
    SIZE_REASON_CONFLICT,
    SIZE_REASON_INVALID_NUMBER,
    SIZE_REASON_NOT_NUMERIC,
    SIZE_REASON_UNKNOWN_UNIT,
    SIZE_SOURCE_PARENTHESIZED,
    SIZE_SOURCE_UNIT,
    SIZE_SOURCE_UNPARSEABLE,
    parse_size,
    parse_size_details,
    parse_temporal,
    normalize_record,
)


def _record(**overrides):
    record = {
        "source": "alpha",
        "source_url": "http://127.0.0.1:5000/alpha/dataset/demo",
        "title": "Example Dataset 2023",
        "category": "Air Quality",
        "publisher": "Atlas Environmental Agency",
        "temporal": "2023-01-01 to 2023-12-31",
        "geography": "California",
        "license": "CC BY 4.0",
        "update_frequency": "每天",
        "fields": [
            {"name": "observed_at", "type": "Timestamp", "description": "Time"},
            {"name": "value", "type": "mystery", "description": "Unknown"},
        ],
        "resources": [
            {"format": "geojson", "size_raw": "760 KB", "url": "http://127.0.0.1:5000/file.geojson"}
        ],
        "extraction": {"fallback_used": False, "fields": {}, "lists": {}},
    }
    record.update(overrides)
    return record


def test_category_frequency_and_license_are_canonicalized(rules):
    normalized, transforms = normalize_record(_record(), rules)
    assert normalized["category"] == "environment"
    assert normalized["update_frequency"] == "daily"
    assert normalized["license"] == "CC-BY-4.0"
    rules_seen = {(item["field_name"], item["rule"]) for item in transforms}
    assert ("category", "category.synonym") in rules_seen
    assert ("update_frequency", "frequency.enum") in rules_seen
    assert ("license", "license.alias") in rules_seen


def test_time_range_is_expanded_and_iso_normalized(rules):
    normalized, _ = normalize_record(_record(temporal="2019 to 2023"), rules)
    assert normalized["temporal_start"] == "2019-01-01"
    assert normalized["temporal_end"] == "2023-12-31"
    assert parse_temporal("2023-12-31 to 2023-01-01")[:2] == ("2023-12-31", "2023-01-01")
    assert parse_temporal("2024")[:2] == ("2024-01-01", "2024-12-31")
    assert parse_temporal("not a date")[:2] == (None, None)


def test_geography_formats_sizes_and_types(rules):
    normalized, transforms = normalize_record(_record(), rules)
    assert normalized["geography"] == "US-CA"
    assert normalized["resources"][0]["format"] == "GEOJSON"
    assert normalized["resources"][0]["size_bytes"] == 778240
    assert normalized["fields"][0]["type"] == "datetime"
    assert normalized["fields"][1]["type"] == "mystery"
    statuses = {(item["field_name"], item["status"]) for item in transforms}
    assert ("geography", "applied") in statuses
    assert ("fields[2].type", "warn") in statuses
    assert parse_size("18,432,000 bytes", rules["size_units"]) == 18432000
    assert parse_size("large", rules["size_units"]) is None
    assert parse_size("1 GB", rules["size_units"]) == 1073741824


def test_invalid_size_and_unknown_category_are_marked_warn(rules):
    normalized, transforms = normalize_record(
        _record(category="Unmapped Topic", resources=[{"format": "csv", "size_raw": "large", "url": "u"}]),
        rules,
    )
    assert normalized["category"] == "unmapped-topic"
    assert normalized["resources"][0]["size_bytes"] is None
    warnings = {item["rule"] for item in transforms if item["status"] == "warn"}
    assert "category.synonym" in warnings
    assert "resource.size" in warnings


def test_global_geography_normalizes_to_global(rules):
    normalized, _ = normalize_record(_record(geography="全球"), rules)
    assert normalized["geography"] == "GLOBAL"


def test_parenthesized_exact_bytes_win_over_unit_conversion(rules):
    units = rules["size_units"]
    result = parse_size_details("12.4 MB (13002342 bytes)", units)
    assert result.size_bytes == 13002342
    assert result.source == SIZE_SOURCE_PARENTHESIZED
    assert result.reason is None
    assert parse_size("12.4 MB (13002342 bytes)", units) == 13002342


def test_size_parsing_is_stable_across_formatting_variants(rules):
    units = rules["size_units"]
    variants = [
        "12.4 MB (13002342 bytes)",
        "13002342 bytes (12.4 MB)",
        "  12.4MB   ( 13,002,342 bytes ) ",
        "(13,002,342 bytes) 12.4 MB",
        "12.4 MB (13,002,342 bytes)",
    ]
    results = [parse_size_details(text, units) for text in variants]
    assert {result.size_bytes for result in results} == {13002342}
    # 来源标签会区分“括号精确值”与“单位换算”，但指纹键相同。
    assert {result.canonical() for result in results} == {"bytes:13002342"}
    assert {result.source for result in results} <= {SIZE_SOURCE_UNIT, SIZE_SOURCE_PARENTHESIZED}
    # 幂等：同一写法重复解析结果完全一致，可直接参与指纹。
    first = parse_size_details("12.4 MB (13002342 bytes)", units)
    assert first == parse_size_details("12.4 MB (13002342 bytes)", units)
    assert first.canonical() == "bytes:13002342"


def test_conflicting_exact_and_converted_values_are_rejected(rules):
    units = rules["size_units"]
    result = parse_size_details("12 MB (12582913 bytes)", units)
    assert result.size_bytes is None
    assert result.source == SIZE_SOURCE_UNPARSEABLE
    assert result.reason == SIZE_REASON_CONFLICT
    assert result.canonical() == f"unparseable:{SIZE_REASON_CONFLICT}"


def test_unknown_unit_and_invalid_number_have_distinct_reasons(rules):
    units = rules["size_units"]
    unknown = parse_size_details("12.4 megabytes", units)
    assert unknown.size_bytes is None
    assert unknown.reason == SIZE_REASON_UNKNOWN_UNIT
    assert unknown.unit == "megabytes"

    invalid = parse_size_details("12.4.5 MB", units)
    assert invalid.size_bytes is None
    assert invalid.reason == SIZE_REASON_INVALID_NUMBER

    text = parse_size_details("large", units)
    assert text.size_bytes is None
    assert text.reason == SIZE_REASON_NOT_NUMERIC

    ambiguous = parse_size_details("12 MB and 3 MB", units)
    assert ambiguous.size_bytes is None
    assert ambiguous.reason == SIZE_REASON_AMBIGUOUS


def test_decimal_binary_distinction_follows_config(rules):
    units = rules["size_units"]
    # 配置中 MB 与 MiB 均为 1048576；调整配置即可改变换算，证明解析器不硬编码。
    assert parse_size_details("1 MB", units).size_bytes == 1048576
    assert parse_size_details("1 MiB", units).size_bytes == 1048576
    custom = {"b": 1, "bytes": 1, "mb": 1000 * 1000, "mib": 1024 * 1024}
    assert parse_size_details("1 MB", custom).size_bytes == 1000000
    assert parse_size_details("1 MiB", custom).size_bytes == 1048576
    assert parse_size_details("1 MB (1000000 bytes)", custom).size_bytes == 1000000


def test_size_fingerprint_is_invariant_to_whitespace_and_order(rules):
    from app.dedupe import content_fingerprint
    from app.normalize import resource_fingerprint_view

    def view(raw: str) -> dict:
        resource = {"format": "CSV", "size_raw": raw, "url": "u"}
        parsed = parse_size_details(raw, rules["size_units"])
        resource["size_bytes"] = parsed.size_bytes
        resource["size_key"] = parsed.canonical()
        return resource_fingerprint_view(resource)

    variants = [
        "12.4 MB (13002342 bytes)",
        "13002342 bytes (12.4 MB)",
        "  12.4MB  ( 13,002,342 bytes )  ",
    ]
    fingerprints = {content_fingerprint(view(raw)) for raw in variants}
    assert len(fingerprints) == 1

    # 同一 size_raw 反复归一化，结果逐字段一致。
    first, _ = normalize_record(_record(resources=[{"format": "csv", "size_raw": variants[0], "url": "u"}]), rules)
    second, _ = normalize_record(_record(resources=[{"format": "csv", "size_raw": variants[0], "url": "u"}]), rules)
    assert first["resources"] == second["resources"]
    assert first["transforms"] == second["transforms"]
    normalized, transforms = normalize_record(
        _record(
            resources=[
                {"format": "csv", "size_raw": "12.4 MB (13002342 bytes)", "url": "u1"},
                {"format": "csv", "size_raw": "760 KB", "url": "u2"},
                {"format": "csv", "size_raw": "large", "url": "u3"},
                {"format": "csv", "size_raw": "12.4 megabytes", "url": "u4"},
            ]
        ),
        rules,
    )
    size_transforms = [item for item in transforms if item["rule"] == "resource.size"]
    assert [item["size_source"] for item in size_transforms] == [
        SIZE_SOURCE_PARENTHESIZED,
        SIZE_SOURCE_UNIT,
        SIZE_SOURCE_UNPARSEABLE,
        SIZE_SOURCE_UNPARSEABLE,
    ]
    assert [item["size_reason"] for item in size_transforms] == [
        None,
        None,
        SIZE_REASON_NOT_NUMERIC,
        SIZE_REASON_UNKNOWN_UNIT,
    ]
    assert [item["status"] for item in size_transforms] == ["applied", "applied", "warn", "warn"]
    assert normalized["resources"][0]["size_bytes"] == 13002342
    assert normalized["resources"][1]["size_bytes"] == 778240
    assert normalized["resources"][2]["size_bytes"] is None
