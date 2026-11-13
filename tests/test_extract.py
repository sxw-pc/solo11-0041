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
