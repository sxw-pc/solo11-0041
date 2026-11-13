from app.normalize import normalize_record, parse_size, parse_temporal


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
