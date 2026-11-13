import requests


def _session():
    session = requests.Session()
    session.trust_env = False
    return session


def test_health_mock_pages_and_control_api(mock_server):
    base = mock_server["base_url"]
    session = _session()
    try:
        health = session.get(base + "/health", timeout=3)
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        catalog = session.get(base + "/alpha/catalog?page=1", timeout=3)
        assert catalog.status_code == 200
        assert "Air Quality Monitoring Stations 2023" in catalog.text
        empty = session.get(base + "/datasets", timeout=3)
        assert empty.status_code == 200
        assert empty.json()["count"] == 0

        created = session.post(base + "/runs", json={"source": "gamma"}, timeout=20)
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["counts"]["datasets"] == 6
        assert payload["errors"] == []

        run = session.get(base + f"/runs/{payload['run_id']}", timeout=3)
        assert run.status_code == 200
        assert run.json()["counts"]["datasets"] == 6

        datasets = session.get(base + "/datasets?category=environment", timeout=3)
        assert datasets.status_code == 200
        assert datasets.json()["count"] == 3
        recent = session.get(
            base + "/datasets?updated_from=2000-01-01&updated_to=2099-01-01", timeout=3
        )
        assert recent.status_code == 200
        assert recent.json()["count"] == 6
        invalid = session.get(base + "/datasets?limit=not-a-number", timeout=3)
        assert invalid.status_code == 400

        exported = session.get(base + "/export?format=md", timeout=5)
        assert exported.status_code == 200
        assert "# 数据集谱系档案" in exported.text
        assert "**来源溯源**" in exported.text
    finally:
        session.close()
