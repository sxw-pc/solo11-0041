from app.extract import extract_detail
from app.pipeline import load_sources


def test_extract_alpha_structural_selectors(fixture_reader, config_dir):
    config = load_sources(config_dir)["sources"]["alpha"]
    record = extract_detail(
        fixture_reader("alpha", "detail-air-quality-2023.html"),
        "http://127.0.0.1:5000/alpha/dataset/air-quality-2023",
        "alpha",
        config,
    )
    assert record["title"] == "Air Quality Monitoring Stations 2023"
    assert record["category"] == "Environment"
    assert record["publisher"] == "Atlas Environmental Agency"
    assert record["temporal"] == "2023-01-01 to 2023-12-31"
    assert record["geography"] == "United States"
    assert len(record["fields"]) == 4
    assert record["fields"][0]["name"] == "station_id"
    assert record["fields"][0]["source_selector"] == "table.fields tbody tr"
    assert len(record["resources"]) == 2
    assert record["resources"][0]["format"] == "CSV"
    assert record["resources"][0]["size_raw"] == "12.4 MB"
    assert record["extraction"]["fallback_used"] is False
    trace = record["extraction"]["fields"]["title"]
    assert trace["selector"] == "h1.dataset-title"
    assert trace["location"]
    assert trace["snippet"].startswith("Air Quality")


def test_extract_gamma_uses_key_value_and_link_fallbacks(fixture_reader, config_dir):
    config = load_sources(config_dir)["sources"]["gamma"]
    record = extract_detail(
        fixture_reader("gamma", "detail-g-001.html"),
        "http://127.0.0.1:5000/gamma/item/g-001",
        "gamma",
        config,
    )
    assert record["title"] == "Lakeview Water Quality Samples"
    assert record["temporal"] == "2020-01-01 to 2023-12-31"
    assert record["geography"] == "Minnesota"
    assert record["license"] == "CC0"
    assert record["update_frequency"] == "Quarterly"
    assert record["extraction"]["fallback_used"] is True
    assert record["extraction"]["fields"]["temporal"]["fallback"] is True
    assert record["extraction"]["lists"]["resources"]["fallback"] is True
    assert record["resources"][0]["format"] == "CSV"
    assert record["resources"][0]["size_raw"] == "4.5 MB"
    assert "a[href$=resource]" in record["resources"][0]["source_selector"]


def test_extract_field_table_heuristic_when_selector_misses():
    config = {
        "detail": {
            "selectors": {"title": "h1"},
            "field_table": {"selector": "table.does-not-exist tbody tr"},
        }
    }
    html = """
    <html><body><h1>Heuristic Table Dataset</h1>
    <table><thead><tr><th>Field</th><th>Data type</th><th>Description</th></tr></thead>
    <tbody><tr><td>code</td><td>string</td><td>Identifier</td></tr>
    <tr><td>value</td><td>decimal</td><td>Observed value</td></tr></tbody></table>
    <a href="/files/example.json">Download dataset (2 MB)</a>
    </body></html>
    """
    record = extract_detail(html, "http://127.0.0.1:5000/item", "demo", config)
    assert len(record["fields"]) == 2
    assert record["fields"][0]["type"] == "string"
    assert record["extraction"]["lists"]["fields"]["fallback"] is True
    assert record["extraction"]["lists"]["resources"]["fallback"] is True
    assert record["resources"][0]["format"] == "JSON"
    assert record["resources"][0]["size_raw"] == "2 MB"


MULTI_CANDIDATE_HTML = """
<html><head><title>Doc</title></head><body>
<nav><a href="/home">Home</a><a href="/cat/breadcrumb">Breadcrumb Category</a></nav>
<article>
  <h1>H1 Headline Title</h1>
  <p class="eyebrow"><span>Selector Category</span></p>
  <p class="byline">Published by <strong>Selector Publisher</strong></p>
  <p class="publisher">Publisher Class Name</p>
  <dl>
    <dt>Category</dt><dd>KV Category Value</dd>
    <dt>Publisher</dt><dd>KV Publisher Value</dd>
    <dt>Time coverage</dt><dd>2020-01-01 to 2020-12-31</dd>
  </dl>
</article>
</body></html>
"""


def test_extract_records_all_distinct_candidates_and_arbitrates_by_score():
    config = {"detail": {"selectors": {
        "title": "article h1",
        "category": "p.eyebrow span",
        "publisher": "p.byline strong",
        "temporal": "dd.missing",
    }}}
    record = extract_detail(MULTI_CANDIDATE_HTML, "http://127.0.0.1:5000/item", "demo", config)
    extraction = record["extraction"]

    category_trace = extraction["fields"]["category"]
    category_values = [candidate["value"] for candidate in category_trace["candidates"]]
    assert category_values == ["Selector Category", "KV Category Value", "Breadcrumb Category"]
    assert category_trace["adopted"] == "selector"
    assert category_trace["fallback"] is False
    adopted = next(c for c in category_trace["candidates"] if c["adopted"])
    assert adopted["path"] == "selector" and adopted["score"] == 100.0
    rejected = {c["value"]: c for c in category_trace["candidates"] if not c["adopted"]}
    assert rejected["KV Category Value"]["rejected_reason"].startswith("score 80.0")
    assert rejected["Breadcrumb Category"]["rejected_reason"].startswith("score 40.0")
    # 落选的启发式旁证不能把该字段标记为回退（整页 fallback 由 temporal 的采用路径决定）
    assert category_trace["fallback"] is False
    assert extraction["fallback_used"] is True
    assert record["category"] == "Selector Category"

    publisher_trace = extraction["fields"]["publisher"]
    publisher_paths = [c["path"] for c in publisher_trace["candidates"]]
    assert publisher_paths == ["selector", "heuristic:key-value", "heuristic:publisher-class"]
    assert all("location" in c and "snippet" in c for c in publisher_trace["candidates"])

    # 无配置 selector 时由启发式仲裁采用，fallback 仅反映最终采用路径
    temporal_trace = extraction["fields"]["temporal"]
    assert temporal_trace["adopted"] == "heuristic:key-value"
    assert temporal_trace["fallback"] is True
    assert temporal_trace["candidates"][0]["match"] == "exact"
    assert record["temporal"] == "2020-01-01 to 2020-12-31"


def test_extract_field_confidence_weights_can_override_selector_priority():
    config = {
        "detail": {
            "selectors": {"publisher": "p.byline strong"},
            "field_confidence": {
                "publisher": {"selector": 10, "heuristic:key-value": 90},
            },
        }
    }
    record = extract_detail(MULTI_CANDIDATE_HTML, "http://127.0.0.1:5000/item", "demo", config)
    trace = record["extraction"]["fields"]["publisher"]
    assert record["publisher"] == "KV Publisher Value"
    assert trace["adopted"] == "heuristic:key-value"
    assert trace["fallback"] is True
    selector_candidate = next(c for c in trace["candidates"] if c["path"] == "selector")
    assert selector_candidate["adopted"] is False
    assert "below adopted score 110.0" in selector_candidate["rejected_reason"]
    # 最终采用启发式 -> 整页 fallback_used 为真
    assert record["extraction"]["fallback_used"] is True


def test_extract_tied_scores_resolved_by_appearance_order():
    html = """
    <html><body><h1>H1 Title</h1>
    <dl><dt>Dataset title</dt><dd>KV Title</dd></dl></body></html>
    """
    config = {
        "detail": {
            "field_confidence": {
                "title": {"heuristic:h1": 40, "heuristic:key-value": 40},
            }
        }
    }
    record = extract_detail(html, "http://127.0.0.1:5000/item", "demo", config)
    trace = record["extraction"]["fields"]["title"]
    assert record["title"] == "H1 Title"
    assert [c["order"] for c in trace["candidates"]] == [1, 2]
    assert trace["candidates"][0]["adopted"] is True
    assert trace["candidates"][1]["rejected_reason"].startswith("tie at score 60.0")
    assert "appearance order" in trace["candidates"][1]["rejected_reason"]


def test_extract_blank_heuristic_keeps_field_level_fallback_trace():
    # 配置 selector 未命中，唯一启发式证据是空键值单元格：无候选但保留回退溯源
    html = "<table><tbody><tr><th>License</th><td></td></tr></tbody></table>"
    config = {"detail": {"selectors": {"license": "td.missing"}}}
    record = extract_detail(html, "http://127.0.0.1:5000/item", "demo", config)
    trace = record["extraction"]["fields"]["license"]
    assert record["license"] is None
    assert trace["candidates"] == []
    assert trace["fallback"] is True
    assert trace["adopted"] == "heuristic:key-value[License]"
    assert record["extraction"]["fallback_used"] is True


def test_extract_identical_values_across_paths_dedupe_to_single_candidate():
    html = """
    <html><body><h1>Same Title</h1>
    <dl><dt>Dataset title</dt><dd>Same Title</dd></dl></body></html>
    """
    record = extract_detail(html, "http://127.0.0.1:5000/item", "demo", {})
    trace = record["extraction"]["fields"]["title"]
    assert len(trace["candidates"]) == 1
    assert trace["candidates"][0]["adopted"] is True
    assert trace["candidates"][0]["rejected_reason"] is None
