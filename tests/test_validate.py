from app.validate import summarize, validate_cross_source, validate_record


def _record(**overrides):
    record = {
        "instance_id": "inst_demo",
        "source": "alpha",
        "title": "Example Dataset",
        "category": "environment",
        "publisher": "Atlas Environmental Agency",
        "temporal_start": "2023-01-01",
        "temporal_end": "2023-12-31",
        "geography": "US",
        "license": "CC-BY-4.0",
        "update_frequency": "daily",
        "fields": [{"name": "value", "type": "number"}],
        "resources": [
            {"format": "CSV", "size_bytes": 1024, "size_raw": "1 KB", "url": "http://127.0.0.1/f.csv"}
        ],
        "extraction": {"fallback_used": False, "fields": {}, "lists": {}},
    }
    record.update(overrides)
    return record


def test_valid_record_has_no_findings(rules):
    findings = validate_record(_record(), rules)
    assert findings == []
    assert summarize(findings)["total"] == 0


def test_missing_required_field_is_error(rules):
    findings = validate_record(_record(license=None), rules)
    errors = [item for item in findings if item["severity"] == "error"]
    assert errors
    assert any(item["rule_id"] == "required.missing" and item["field_name"] == "license" for item in errors)


def test_enum_and_reversed_temporal_are_errors(rules):
    findings = validate_record(
        _record(category="weird", update_frequency="fortnightly", temporal_start="2024-01-01",
                temporal_end="2023-01-01"),
        rules,
    )
    rules_seen = {item["rule_id"] for item in findings if item["severity"] == "error"}
    assert {"enum.category", "enum.update_frequency", "temporal.start_after_end"} <= rules_seen


def test_warning_levels_for_size_type_and_fallback(rules):
    record = _record(
        fields=[{"name": "value", "type": "mystery"}],
        resources=[{"format": "CSV", "size_bytes": None, "size_raw": "large",
                    "url": "http://127.0.0.1/f.csv"}],
        extraction={
            "fallback_used": True,
            "fields": {"temporal": {"fallback": True, "adopted": "heuristic:key-value"}},
            "lists": {"resources": {"fallback": True}},
        },
    )
    findings = validate_record(record, rules)
    warnings = {item["rule_id"] for item in findings if item["severity"] == "warn"}
    assert {"field.type_unknown", "resource.size_not_numeric", "extraction.fallback_used"} <= warnings


def test_fallback_evidence_reads_adopted_trace_only(rules):
    # category 的启发式候选落选（selector 采用），不得出现在 fallback 证据里；
    # temporal 最终采用启发式，必须带上具体字段名。
    record = _record(
        extraction={
            "fallback_used": True,
            "fields": {
                "category": {
                    "fallback": False,
                    "adopted": "selector",
                    "candidates": [
                        {"path": "selector", "adopted": True, "value": "Environment"},
                        {"path": "heuristic:breadcrumb", "adopted": False, "value": "Env"},
                    ],
                },
                "temporal": {
                    "fallback": True,
                    "adopted": "heuristic:key-value",
                    "candidates": [
                        {"path": "heuristic:key-value", "adopted": True, "value": "2020"},
                    ],
                },
            },
            "lists": {},
        }
    )
    findings = validate_record(record, rules)
    fallback = [item for item in findings if item["rule_id"] == "extraction.fallback_used"]
    assert len(fallback) == 1
    assert "temporal" in fallback[0]["evidence"]
    assert "category" not in fallback[0]["evidence"]


def test_cross_source_field_gap_is_info(rules):
    first = {"source": "alpha", "fields": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]}
    second = {"source": "beta", "fields": [{"name": str(i)} for i in range(8)]}
    findings = validate_cross_source([first, second], rules)
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"
    assert findings[0]["rule_id"] == "cross_source.field_count_gap"
    assert validate_cross_source([first, {**second, "source": "alpha"}], rules) == []
