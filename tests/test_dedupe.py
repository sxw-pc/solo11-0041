from app.dedupe import group_instances
from app.normalize import normalize_title

CONFIG = {
    "title_similarity_threshold": 0.86,
    "require_same_publisher": True,
    "require_same_period": True,
    "require_same_geography": True,
    "field_priority": ["alpha", "beta", "gamma"],
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
