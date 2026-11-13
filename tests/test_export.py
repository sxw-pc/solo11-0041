import json
from pathlib import Path

from app.export import export_archive
from app.pipeline import run_pipeline


def _build_archive(tmp_path, fixtures_dir, config_dir):
    db_path = tmp_path / "archive.db"
    result = run_pipeline(
        "all",
        replay_from=fixtures_dir,
        db_path=db_path,
        data_dir=tmp_path / "data",
        config_dir=config_dir,
    )
    assert result["counts"]["datasets"] == 15
    return Path(result["export_dir"]), db_path


def test_export_json_markdown_and_stats(tmp_path, fixtures_dir, config_dir):
    export_dir, db_path = _build_archive(tmp_path, fixtures_dir, config_dir)
    archive = json.loads((export_dir / "archive.json").read_text(encoding="utf-8"))
    stats = json.loads((export_dir / "stats.json").read_text(encoding="utf-8"))
    markdown = (export_dir / "archive.md").read_text(encoding="utf-8")

    assert archive["schema_version"] == "1.0"
    assert archive["stats"]["dataset_count"] == 15
    assert stats == archive["stats"]
    sample = archive["datasets"][0]
    required = {
        "dataset_uid", "title", "category", "publisher", "temporal_start", "temporal_end",
        "geography", "license", "update_frequency", "fields", "resources", "instances",
        "validation_findings", "provenance",
    }
    assert required <= set(sample)
    assert sample["provenance"][0]["raw_archive"]["sha256"]
    assert "## 告警摘要" in markdown
    assert "## environment" in markdown
    assert "**来源溯源**" in markdown
    assert "raw_archive" not in markdown
    assert "sha256" in markdown
    assert "| 数据集 | 15 |" in markdown

    manual = export_archive(str(db_path), tmp_path / "manual")
    assert Path(manual["json"]).exists()
    assert Path(manual["md"]).exists()
    assert Path(manual["stats"]).exists()
