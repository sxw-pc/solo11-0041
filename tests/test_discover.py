from types import SimpleNamespace

from app.discover import discover_source


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch(self, url, source, kind):
        self.calls.append((url, source, kind))
        return SimpleNamespace(html=self.pages[url], url=url)


def _config(**pagination):
    return {
        "base_url": "http://127.0.0.1:5000",
        "start_path": "/catalog",
        "pagination": {"type": "query", "param": "page", "start": 1, "step": 1, "max_pages": 2,
                       **pagination},
        "list": {"item_selector": "div.item", "link_selector": "a", "item_id_regex": "([^/?#]+)/?$"},
    }


def test_query_pagination_deduplicates_detail_urls():
    pages = {
        "http://127.0.0.1:5000/catalog?page=1": (
            "<div class='item'><a href='/dataset/a'>A</a></div>"
            "<div class='item'><a href='/dataset/a#fragment'>A again</a></div>"
            "<div class='item'><a href='/dataset/b'>B</a></div>"
        ),
        "http://127.0.0.1:5000/catalog?page=2": (
            "<div class='item'><a href='/dataset/c'>C</a></div>"
        ),
    }
    fetcher = FakeFetcher(pages)
    result = discover_source(fetcher.fetch, "demo", _config())
    assert result.page_count == 2
    assert [entry["url"] for entry in result.entries] == [
        "http://127.0.0.1:5000/dataset/a",
        "http://127.0.0.1:5000/dataset/b",
        "http://127.0.0.1:5000/dataset/c",
    ]
    assert [entry["item_id"] for entry in result.entries] == ["a", "b", "c"]
    assert all(call[2] == "list" for call in fetcher.calls)


def test_max_pages_is_a_hard_limit():
    pages = {
        "http://127.0.0.1:5000/catalog?page=1": "<div class='item'><a href='/dataset/a'>A</a></div>",
        "http://127.0.0.1:5000/catalog?page=2": "<div class='item'><a href='/dataset/b'>B</a></div>",
    }
    fetcher = FakeFetcher(pages)
    result = discover_source(fetcher.fetch, "demo", _config(max_pages=1))
    assert result.page_count == 1
    assert result.discovered == 1
    assert len(fetcher.calls) == 1


def test_next_link_pagination_stops_on_loop():
    config = {
        "base_url": "http://127.0.0.1:5000",
        "start_path": "/next-1",
        "pagination": {"type": "next_link", "next_selector": "a.next", "max_pages": 5},
        "list": {"item_selector": "article", "link_selector": "a.record"},
    }
    pages = {
        "http://127.0.0.1:5000/next-1": (
            "<article><a class='record' href='/item/1'>One</a></article>"
            "<a class='next' href='/next-2'>Next</a>"
        ),
        "http://127.0.0.1:5000/next-2": (
            "<article><a class='record' href='/item/2'>Two</a></article>"
            "<a class='next' href='/next-1'>Loop</a>"
        ),
    }
    fetcher = FakeFetcher(pages)
    result = discover_source(fetcher.fetch, "demo", config)
    assert result.page_count == 2
    assert result.discovered == 2
    assert len(fetcher.calls) == 2


def test_empty_list_page_stops_pagination():
    pages = {"http://127.0.0.1:5000/catalog?page=1": "<p>nothing here</p>"}
    fetcher = FakeFetcher(pages)
    result = discover_source(fetcher.fetch, "demo", _config(max_pages=3))
    assert result.page_count == 1
    assert result.entries == []
