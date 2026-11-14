from app.dedupe import group_instances
from app.normalize import normalize_record, normalize_title

CONFIG = {
    "title_similarity_threshold": 0.86,
    "require_same_publisher": True,
    "require_same_period": True,
    "require_same_geography": True,
    "field_priority": ["alpha", "beta", "gamma"],
}

RULES = {
    "categories": {"environment": ["environment", "environmental", "environmental monitoring"]},
    "frequency": {"daily": ["daily", "every day", "day"]},
    "geography": {"US": ["united states", "us", "usa"]},
}


def _instance(source, title, publisher="Atlas Environmental Agency", start="2023-01-01",
              end="2023-12-31", field_count=4, url=None):
    return {
        "source": source,
        "source_url": url or f"http://127.0.0.1:5000/{source}/dataset/air",
        "instance_id": f"inst_{source}",
        "title": title,
        "title_norm": normalize_title(title),
        "publisher": publisher,
        "publisher_norm": normalize_title(publisher),
        "temporal_start": start,
        "temporal_end": end,
        "geography": "US",
        "license": "CC-BY-4.0",
        "category": "environment",
        "update_frequency": "daily",
        "fields": [{"name": f"f{i}", "type": "string"} for i in range(field_count)],
        "resources": [{"format": "CSV", "size_raw": "1 MB", "url": "u"}],
    }


def test_exact_duplicate_across_sources_merges_with_alpha_priority():
    alpha = _instance("alpha", "Air Quality Monitoring Stations 2023")
    beta = _instance("beta", "Air Quality Monitoring Stations (2023)", field_count=8)
    groups = group_instances([beta, alpha], CONFIG)
    assert len(groups) == 1
    group = groups[0]
    assert group["source_count"] == 2
    assert group["primary_source"] == "alpha"
    assert group["title"] == "Air Quality Monitoring Stations 2023"
    assert len(group["fields"]) == 4
    assert group["score"] == 1.0
    assert group["basis"] == "exact_title+publisher+period"
    assert group["dataset_uid"].startswith("ds_")
    assert len(group["merge_decision"]["member_refs"]) == 2


def test_fuzzy_title_matches_when_other_fingerprint_parts_agree():
    alpha = _instance("alpha", "Air Quality Monitoring Stations 2023")
    beta = _instance("beta", "Air Quality Monitoring Station 2023")
    groups = group_instances([alpha, beta], CONFIG)
    assert len(groups) == 1
    assert groups[0]["basis"] == "fuzzy_title+publisher+period"
    assert groups[0]["score"] < 1.0


def test_different_publisher_or_period_does_not_merge():
    alpha = _instance("alpha", "Air Quality Monitoring Stations 2023")
    different_publisher = _instance("beta", "Air Quality Monitoring Stations 2023", publisher="Other Agency")
    different_period = _instance("gamma", "Air Quality Monitoring Stations 2023", start="2022-01-01", end="2022-12-31")
    groups = group_instances([alpha, different_publisher, different_period], CONFIG)
    assert len(groups) == 3
    assert all(group["source_count"] == 1 for group in groups)


def test_same_source_instances_are_not_merged():
    first = _instance("alpha", "Air Quality Monitoring Stations 2023", url="http://127.0.0.1/a")
    second = _instance("alpha", "Air Quality Monitoring Stations 2023", url="http://127.0.0.1/b")
    groups = group_instances([first, second], CONFIG)
    assert len(groups) == 2


def _normalized_instance(source, url, title, *, category, frequency, geography):
    """走真实 normalize_record，保留 warn 降级状态与 transforms 审计轨迹。"""
    record = {
        "source": source,
        "source_url": url,
        "title": title,
        "publisher": "Atlas Environmental Agency",
        "temporal": "2023-01-01 to 2023-12-31",
        "category": category,
        "update_frequency": frequency,
        "geography": geography,
        "license": "CC BY 4.0",
        "fields": [{"name": "station_id", "type": "string"}],
        "resources": [{"format": "CSV", "size_raw": "1 MB", "url": "u"}],
    }
    normalized, transforms = normalize_record(record, RULES)
    normalized["instance_id"] = f"inst_{source}"
    normalized["transforms"] = transforms
    return normalized


def test_warn_degraded_member_marks_inherited_degraded_with_primary_value_kept():
    alpha = _normalized_instance(
        "alpha", "http://127.0.0.1:5000/alpha/dataset/air",
        "Air Quality Monitoring Stations 2023",
        category="Environment", frequency="Daily", geography="United States",
    )
    beta = _normalized_instance(
        "beta", "http://127.0.0.1:5000/beta/dataset/air",
        "Air Quality Monitoring Stations (2023)",
        category="Environmental Monitoring Network", frequency="Continuous-ish",
        geography="United States",
    )
    # 前置条件：beta 的 category/frequency 确实落到 warn 兜底值
    assert beta["normalization_status"]["category"]["status"] == "warn"
    assert beta["normalization_status"]["update_frequency"]["status"] == "warn"
    assert beta["category"] == "environmental-monitoring-network"
    assert beta["update_frequency"] == "unknown"
    assert alpha["normalization_status"]["category"]["status"] == "applied"

    groups = group_instances([beta, alpha], CONFIG)
    assert len(groups) == 1
    group = groups[0]

    # 数据集层仍取主来源 alpha 的规范值
    assert group["primary_source"] == "alpha"
    assert group["category"] == "environment"
    assert group["update_frequency"] == "daily"
    assert group["geography"] == "US"

    # 仅 warn 降级的两个字段被标记为继承降级；geography 双方都规范，不标记
    assert group["inherited_degraded_fields"] == ["category", "update_frequency"]

    detail = group["merge_decision"]["detail"]
    assert detail["inherited_degraded_fields"] == ["category", "update_frequency"]
    lineage = {member["source"]: member for member in detail["member_lineage"]}
    assert set(lineage) == {"alpha", "beta"}
    assert lineage["alpha"]["is_primary"] is True
    assert lineage["beta"]["is_primary"] is False

    beta_category = lineage["beta"]["fields"]["category"]
    assert beta_category == {
        "rule": "category.synonym",
        "status": "warn",
        "raw_value": "Environmental Monitoring Network",
        "normalized_value": "environmental-monitoring-network",
    }
    beta_frequency = lineage["beta"]["fields"]["update_frequency"]
    assert beta_frequency["status"] == "warn"
    assert beta_frequency["rule"] == "frequency.enum"
    assert beta_frequency["normalized_value"] == "unknown"
    alpha_category = lineage["alpha"]["fields"]["category"]
    assert alpha_category["status"] == "applied"
    assert alpha_category["raw_value"] == "Environment"
    assert alpha_category["normalized_value"] == "environment"
    assert lineage["alpha"]["fields"]["geography"]["status"] == "applied"
    assert lineage["beta"]["fields"]["geography"]["status"] == "applied"


def test_primary_own_warn_degradation_is_not_marked_inherited():
    # alpha 自己 category 就是 warn 兜底值；即便 beta 也 warn，也不能算“继承来的降级”
    alpha = _normalized_instance(
        "alpha", "http://127.0.0.1:5000/alpha/dataset/air",
        "Air Quality Monitoring Stations 2023",
        category="Environmental Monitoring Network", frequency="Daily", geography="United States",
    )
    beta = _normalized_instance(
        "beta", "http://127.0.0.1:5000/beta/dataset/air",
        "Air Quality Monitoring Stations (2023)",
        category="Ecology Watch", frequency="Daily", geography="United States",
    )
    assert alpha["normalization_status"]["category"]["status"] == "warn"
    groups = group_instances([beta, alpha], CONFIG)
    assert len(groups) == 1
    group = groups[0]
    assert group["primary_source"] == "alpha"
    assert group["category"] == "environmental-monitoring-network"
    assert group["inherited_degraded_fields"] == []
    assert group["merge_decision"]["detail"]["inherited_degraded_fields"] == []
    # 谱系仍完整保留两个成员的 warn 原因
    for source in ("alpha", "beta"):
        entry = next(m for m in group["merge_decision"]["detail"]["member_lineage"]
                     if m["source"] == source)
        assert entry["fields"]["category"]["status"] == "warn"
        assert entry["fields"]["category"]["rule"] == "category.synonym"


def test_missing_member_field_is_recorded_but_not_treated_as_inherited_degradation():
    alpha = _normalized_instance(
        "alpha", "http://127.0.0.1:5000/alpha/dataset/air",
        "Air Quality Monitoring Stations 2023",
        category="Environment", frequency="Daily", geography="United States",
    )
    beta = _normalized_instance(
        "beta", "http://127.0.0.1:5000/beta/dataset/air",
        "Air Quality Monitoring Stations (2023)",
        category="Environmental Monitoring Network", frequency="", geography="United States",
    )
    assert beta["normalization_status"]["update_frequency"]["status"] == "missing"
    groups = group_instances([beta, alpha], CONFIG)
    group = groups[0]
    # missing 不是 warn 降级，不进 inherited_degraded_fields；只有 category 被标记
    assert group["inherited_degraded_fields"] == ["category"]
    beta_lineage = next(m for m in group["merge_decision"]["detail"]["member_lineage"]
                        if m["source"] == "beta")
    assert beta_lineage["fields"]["update_frequency"]["status"] == "missing"
    assert beta_lineage["fields"]["update_frequency"]["rule"] is None
    assert beta_lineage["fields"]["update_frequency"]["normalized_value"] is None
