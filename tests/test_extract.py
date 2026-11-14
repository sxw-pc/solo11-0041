import json
import sqlite3

import pytest

from app.extract import extract_detail
from app.pipeline import load_sources, run_pipeline


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


def test_invalid_scalar_selector_is_isolated_and_recorded_in_errors():
    config = {
        "detail": {
            "selectors": {
                "title": "h1",
                "publisher": "p.publisher",
                "temporal": "!!!invalid-selector",
            }
        }
    }
    html = """
    <html><body><h1>Partial Parse Dataset</h1>
    <p class="publisher">Civic Data Office</p>
    </body></html>
    """
    record = extract_detail(html, "http://127.0.0.1:5000/item/1", "demo", config)

    # 非法选择器只让该标量字段按缺失处理，其余字段照常产出
    assert record["title"] == "Partial Parse Dataset"
    assert record["publisher"] == "Civic Data Office"
    assert record["temporal"] is None
    assert "temporal" not in record["extraction"]["fields"]

    errors = record["extraction"]["errors"]
    assert len(errors) == 1
    assert errors[0]["field"] == "temporal"
    assert errors[0]["type"] == "SelectorSyntaxError"
    assert errors[0]["message"]
    # 未失败的选择器轨迹保持结构化、不被标记为回退（fallback_used 基线行为不变）
    title_trace = record["extraction"]["fields"]["title"]
    assert title_trace["selector"] == "h1"
    assert title_trace["fallback"] is False
    assert record["extraction"]["fields"]["publisher"]["fallback"] is False
    # 无错误时 errors 始终是空列表
    healthy = extract_detail(
        html, "http://127.0.0.1:5000/item/2", "demo",
        {"detail": {"selectors": {"title": "h1"}}},
    )
    assert healthy["extraction"]["errors"] == []


def test_invalid_field_and_resource_list_selectors_yield_empty_lists():
    config = {
        "detail": {
            "selectors": {"title": "h1"},
            "field_table": {
                "selector": "table.fields tbody tr",
                "name_selector": "!!!bad-name",
            },
            "resources": {
                "selector": "ul.resources li",
                "format_selector": "!!!bad-format",
            },
        }
    }
    html = """
    <html><body><h1>List Failure Dataset</h1>
    <table class="fields"><tbody><tr><td>code</td><td>string</td></tr></tbody></table>
    <ul class="resources"><li><a href="/files/data.csv">data.csv</a></li></ul>
    </body></html>
    """
    record = extract_detail(html, "http://127.0.0.1:5000/item", "demo", config)

    assert record["title"] == "List Failure Dataset"
    # 列表整体异常按空列表产出，且不触发启发式回退
    assert record["fields"] == []
    assert record["resources"] == []
    assert record["extraction"]["lists"]["fields"] == {
        "selector": "table.fields tbody tr", "fallback": False, "count": 0,
    }
    assert record["extraction"]["lists"]["resources"] == {
        "selector": "ul.resources li", "fallback": False, "count": 0,
    }
    assert record["extraction"]["fallback_used"] is False
    error_fields = {item["field"]: item["type"] for item in record["extraction"]["errors"]}
    assert error_fields == {"fields": "SelectorSyntaxError", "resources": "SelectorSyntaxError"}


def test_title_missing_from_structured_selector_and_h1_fails_page_into_source_errors(
    tmp_path, config_dir
):
    replay_root = tmp_path / "replay"
    replay_root.mkdir()
    detail_path = replay_root / "detail-no-title.html"
    detail_path.write_text(
        "<html><body><p>没有 h1 也没有结构化标题节点的详情页</p></body></html>",
        encoding="utf-8",
    )
    manifest = {
        "source": "alpha",
        "entries": [
            {
                "source": "alpha",
                "url": "http://127.0.0.1:5000/alpha/dataset/no-title",
                "kind": "detail",
                "item_id": "no-title",
                "file": "detail-no-title.html",
                "status_code": 200,
            }
        ],
    }
    (replay_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    db_path = tmp_path / "fail.db"
    data_dir = tmp_path / "data"
    with pytest.raises(Exception):
        extract_detail(
            detail_path.read_text(encoding="utf-8"),
            "http://127.0.0.1:5000/alpha/dataset/no-title",
            "alpha",
            load_sources(config_dir)["sources"]["alpha"],
        )

    result = run_pipeline(
        "alpha",
        replay_from=replay_root,
        db_path=db_path,
        data_dir=data_dir,
        config_dir=config_dir,
    )
    # 页级失败进入 pipeline 的 source_errors 通道，source 运行状态判为 partial
    assert len(result["errors"]) == 1
    assert "no-title" in result["errors"][0]
    assert result["sources"]["alpha"]["status"] == "partial"
    assert result["sources"]["alpha"]["extracted"] == 0

    conn = sqlite3.connect(db_path)
    try:
        status, error = conn.execute(
            "SELECT status, error FROM crawl_run WHERE source='alpha'"
        ).fetchone()
    finally:
        conn.close()
    assert status == "partial"
    assert error and "title" in error

