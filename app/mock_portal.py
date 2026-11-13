"""三个风格不同的本地 mock 数据门户。

所有页面均由本进程渲染，监听地址由 app/server.py 固定为回环地址。
它们只提供目录页、分页与详情页，不提供真实数据文件下载。
"""

from __future__ import annotations

from html import escape

from flask import Blueprint, abort, request

from .mock_content import DATASETS, PAGE_SIZE

bp = Blueprint("portal", __name__)

ALPHA_CSS = """
body{margin:0;background:#eef3f6;color:#17313b;font:16px/1.55 system-ui,sans-serif}
.top{background:#103b4a;color:#fff;padding:18px 6vw}.brand{font-weight:800;letter-spacing:.12em}
.wrap{max-width:960px;margin:36px auto;padding:0 20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}
.dataset-card{background:#fff;border-radius:14px;padding:20px;box-shadow:0 8px 24px #103b4a14;border-top:4px solid #26a69a}
.dataset-card h3{margin:.2em 0}.dataset-card a{color:#0d5c63;text-decoration:none}.badge{background:#d9f3ef;color:#0d5c63;padding:3px 9px;border-radius:99px;font-size:.8rem}
.card-meta{font-size:.88rem;color:#51666d}.pagination{display:flex;gap:12px;margin-top:28px}.pagination a{color:#0d5c63}
h1.dataset-title{font-size:2rem;margin:.2em 0}.meta{display:grid;grid-template-columns:180px 1fr;gap:8px;background:#fff;padding:18px;border-radius:12px}
.meta dt{font-weight:700;color:#51666d}.meta dd{margin:0}.field-table{width:100%;border-collapse:collapse;background:#fff}
.field-table th,.field-table td{text-align:left;padding:9px 12px;border-bottom:1px solid #e0e8eb}.resource-list{padding-left:0;list-style:none}
.resource{background:#fff;padding:12px;margin:8px 0;border-radius:10px}.resource a{color:#0d5c63}.muted{color:#657a81;font-size:.9rem}
"""

BETA_CSS = """
body{margin:0;background:#111820;color:#d9e2ea;font:14px/1.45 "Courier New",monospace}
.topbar{background:#1f6feb;color:#fff;padding:14px 4vw;letter-spacing:.08em}.wrap{max-width:1080px;margin:26px auto;padding:0 18px}
.catalog-table{width:100%;border-collapse:collapse;background:#17222d}.catalog-table th,.catalog-table td{padding:10px;border:1px solid #2b3a49;text-align:left}
.catalog-table a{color:#79c0ff}.catalog-table tr:nth-child(even){background:#131d26}.pagination{margin:18px 0}.pagination a{color:#79c0ff}
#record-title{color:#fff;font-size:1.65rem}.kicker,.agency{color:#8b98a5}.kv{border-collapse:collapse;background:#17222d;min-width:520px}
.kv th,.kv td{border:1px solid #2b3a49;padding:8px 10px;text-align:left}.kv th{color:#9fb0c0;width:180px}
#schema{width:100%;border-collapse:collapse;background:#17222d}#schema th,#schema td{border:1px solid #2b3a49;padding:8px}
.downloads{padding-left:18px}.downloads .fmt{color:#7ee787}.downloads .size{color:#f2cc60}.downloads a{color:#79c0ff}
"""

GAMMA_CSS = """
body{margin:0;background:#fbf8f1;color:#2c2233;font:17px/1.65 Georgia,serif}
header{padding:22px 7vw;border-bottom:3px double #6b4b8a;color:#52356b}.wrap{max-width:880px;margin:34px auto;padding:0 22px}
.eyebrow{color:#8a6aa6;text-transform:uppercase;letter-spacing:.1em;font-size:.8rem}.dataset-brief{border-bottom:1px dotted #b7a4c7;padding:18px 0}
.dataset-brief h2{margin:.2em 0}.dataset-brief a{color:#45215e;text-decoration:none}.next-page{display:inline-block;margin-top:22px;color:#5b337c}
article.record{background:#fffdf8;border:1px solid #d9cfe0;padding:30px;box-shadow:6px 6px 0 #e8dfef}.record-facts{display:grid;grid-template-columns:170px 1fr}
.record-facts dt{font-weight:bold;color:#6b4b8a}.record-facts dd{margin:0 0 8px}.schema-grid{width:100%;border-collapse:collapse}
.schema-grid th,.schema-grid td{text-align:left;border-bottom:1px solid #e4dbea;padding:7px 8px}
.downloads a{color:#5b337c}.byline{color:#6e6275}
"""


def _page(raw: str | None, default: int = 1) -> int:
    try:
        return max(1, int(raw or default))
    except (TypeError, ValueError):
        return default


def _source_page(source: str, page: int) -> tuple[list[dict], int, int]:
    size = PAGE_SIZE[source]
    items = DATASETS[source]
    total = max(1, (len(items) + size - 1) // size)
    return items[(page - 1) * size : page * size], page, total


def _head(title: str, css: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
        f"<style>{css}</style></head><body>"
    )


def _alpha_catalog(page: int) -> str:
    items, page, total = _source_page("alpha", page)
    cards = []
    for item in items:
        href = f"/alpha/dataset/{item['id']}"
        cards.append(
            "<div class='dataset-card'>"
            f"<h3><a href='{href}'>{escape(item['title'])}</a></h3>"
            f"<p class='card-meta'><span class='badge'>{escape(item['category'])}</span> "
            f"{escape(item['publisher'])}</p>"
            f"<p class='muted'>{escape(item['temporal'])} · {len(item['fields'])} fields</p>"
            f"<a href='{href}'>Open record →</a></div>"
        )
    nav = [f"<span>Page {page} of {total}</span>"]
    if page > 1:
        nav.append(f"<a href='/alpha/catalog?page={page - 1}'>Previous</a>")
    if page < total:
        nav.append(f"<a class='next' href='/alpha/catalog?page={page + 1}'>Next →</a>")
    return (
        _head("Atlas Open Data — Catalog", ALPHA_CSS)
        + "<header class='top'><div class='brand'>ATLAS / OPEN DATA</div></header>"
        + "<main class='wrap'><p class='muted'>Curated public-interest datasets</p>"
        + f"<h1>Dataset catalog</h1><div class='grid'>{''.join(cards)}</div>"
        + f"<nav class='pagination'>{' '.join(nav)}</nav></main></body></html>"
    )


def _alpha_detail(item: dict) -> str:
    rows = "".join(
        f"<tr><td>{escape(f['name'])}</td><td>{escape(f['type'])}</td><td>{escape(f['description'])}</td></tr>"
        for f in item["fields"]
    )
    resources = "".join(
        f"<li class='resource'><span class='resource-format'>{escape(r['format'])}</span> "
        f"<span class='resource-size'>{escape(r['size'])}</span> "
        f"<a href='{escape(r['href'])}'>Download {escape(r['format'])}</a></li>"
        for r in item["resources"]
    )
    return (
        _head(item["title"], ALPHA_CSS)
        + "<header class='top'><div class='brand'>ATLAS / OPEN DATA</div></header>"
        + f"<main class='wrap'><p class='muted'><a href='/alpha/catalog'>← Catalog</a></p>"
        + f"<span class='category'>{escape(item['category'])}</span>"
        + f"<h1 class='dataset-title'>{escape(item['title'])}</h1>"
        + f"<p class='publisher-name'>{escape(item['publisher'])}</p>"
        + f"<p>{escape(item['description'])}</p>"
        + "<dl class='meta'>"
        + f"<dt>Time period</dt><dd data-field='temporal'>{escape(item['temporal'])}</dd>"
        + f"<dt>Geographic scope</dt><dd data-field='geography'>{escape(item['geography'])}</dd></dl>"
        + f"<p>License: <span class='license'>{escape(item['license'])}</span></p>"
        + f"<p>Update cadence: <span class='frequency'>{escape(item['frequency'])}</span></p>"
        + "<h2>Schema</h2><table class='fields'><thead><tr><th>Field</th><th>Type</th>"
        + f"<th>Description</th></tr></thead><tbody>{rows}</tbody></table>"
        + f"<h2>Resources</h2><ul class='resource-list'>{resources}</ul></main></body></html>"
    )


@bp.get("/")
def home() -> str:
    return (
        _head("Datasheet Probe Mock Portals", ALPHA_CSS)
        + "<header class='top'><div class='brand'>DATASHEET PROBE / MOCK NETWORK</div></header>"
        + "<main class='wrap'><h1>Local mock data portals</h1><ul>"
        + "<li><a href='/alpha/catalog'>Atlas Open Data</a> — card catalogue</li>"
        + "<li><a href='/beta/browse'>Harbor Data Exchange</a> — dense tabular catalogue</li>"
        + "<li><a href='/gamma/browse'>Lakeview Civic Lakehouse</a> — document-style catalogue</li>"
        + "</ul><p class='muted'>All resources are synthetic and served only on loopback.</p>"
        + "</main></body></html>"
    )


@bp.get("/alpha/catalog")
def alpha_catalog() -> str:
    return _alpha_catalog(_page(request.args.get("page")))


@bp.get("/alpha/dataset/<slug>")
def alpha_dataset(slug: str) -> str:
    item = next((d for d in DATASETS["alpha"] if d["id"] == slug), None)
    if item is None:
        abort(404)
    return _alpha_detail(item)


@bp.get("/beta/browse")
def beta_browse() -> str:
    page = _page(request.args.get("page"))
    items, page, total = _source_page("beta", page)
    rows = "".join(
        "<tr class='record-row'>"
        f"<td><a href='/beta/record/{item['id']}'>{escape(item['title'])}</a></td>"
        f"<td>{escape(item['category'])}</td><td>{escape(item['publisher'])}</td>"
        f"<td>{escape(item['temporal'])}</td></tr>"
        for item in items
    )
    pager = [f"<span>{page}/{total}</span>"]
    if page > 1:
        pager.append(f"<a href='/beta/browse?page={page - 1}'>previous</a>")
    if page < total:
        pager.append(f"<a class='next-page' href='/beta/browse?page={page + 1}'>next &gt;</a>")
    return (
        _head("Harbor Data Exchange", BETA_CSS)
        + "<header class='topbar'>HARBOR DATA EXCHANGE :: CATALOG</header>"
        + "<main class='wrap'><h1>Available records</h1>"
        + "<table class='catalog-table'><thead><tr><th>Record</th><th>Category</th>"
        + f"<th>Publisher</th><th>Period</th></tr></thead><tbody>{rows}</tbody></table>"
        + f"<nav class='pagination'>{' '.join(pager)}</nav></main></body></html>"
    )


@bp.get("/beta/record/<slug>")
def beta_record(slug: str) -> str:
    item = next((d for d in DATASETS["beta"] if d["id"] == slug), None)
    if item is None:
        abort(404)
    rows = "".join(
        f"<tr><td>{escape(f['name'])}</td><td>{escape(f['type'])}</td><td>{escape(f['description'])}</td></tr>"
        for f in item["fields"]
    )
    downloads = "".join(
        f"<li><a href='{escape(r['href'])}'>{escape(r['format'])} extract</a> "
        f"<span class='fmt'>{escape(r['format'])}</span> "
        f"<span class='size'>{escape(r['size'])}</span></li>"
        for r in item["resources"]
    )
    return (
        _head(item["title"], BETA_CSS)
        + "<header class='topbar'>HARBOR DATA EXCHANGE :: RECORD</header>"
        + "<main class='wrap'><p><a href='/beta/browse'>index</a> / record</p>"
        + f"<h1 id='record-title'>{escape(item['title'])}</h1>"
        + f"<p class='kicker'>Category <span>{escape(item['category'])}</span></p>"
        + f"<p class='agency'>{escape(item['publisher'])}</p><p>{escape(item['description'])}</p>"
        + "<table class='kv'><tbody>"
        + f"<tr><th>Time span</th><td>{escape(item['temporal'])}</td></tr>"
        + f"<tr><th>Geography</th><td>{escape(item['geography'])}</td></tr>"
        + f"<tr><th>License</th><td>{escape(item['license'])}</td></tr>"
        + f"<tr><th>Refresh cadence</th><td>{escape(item['frequency'])}</td></tr>"
        + "</tbody></table><h2>Columns</h2><table id='schema'><thead><tr><th>Field</th>"
        + f"<th>Type</th><th>Description</th></tr></thead><tbody>{rows}</tbody></table>"
        + f"<h2>Downloads</h2><ul class='downloads'>{downloads}</ul></main></body></html>"
    )


@bp.get("/gamma/browse")
def gamma_browse() -> str:
    page = _page(request.args.get("after"))
    page = 1 if page == 1 else page
    size = PAGE_SIZE["gamma"]
    items_all = DATASETS["gamma"]
    if page == 1:
        offset = 0
    else:
        offset = min(page, len(items_all))
    items = items_all[offset : offset + size]
    has_next = offset + size < len(items_all)
    briefs = "".join(
        "<article class='dataset-brief'>"
        f"<p class='eyebrow'><span>{escape(item['category'])}</span></p>"
        f"<h2><a href='/gamma/item/{item['id']}'>{escape(item['title'])}</a></h2>"
        f"<p>{escape(item['description'])}</p>"
        f"<p class='byline'>From {escape(item['publisher'])} · {escape(item['temporal'])}</p>"
        "</article>"
        for item in items
    )
    next_link = ""
    if has_next:
        next_link = f"<a class='next-page' href='/gamma/browse?after={offset + size}'>Older records →</a>"
    return (
        _head("Lakeview Civic Lakehouse", GAMMA_CSS)
        + "<header><strong>LAKEVIEW CIVIC LAKEHOUSE</strong> · open register</header>"
        + "<main class='wrap'><h1>Regional data register</h1>"
        + f"{briefs}{next_link}</main></body></html>"
    )


@bp.get("/gamma/item/<slug>")
def gamma_item(slug: str) -> str:
    item = next((d for d in DATASETS["gamma"] if d["id"] == slug), None)
    if item is None:
        abort(404)
    rows = "".join(
        f"<tr><td>{escape(f['name'])}</td><td>{escape(f['type'])}</td><td>{escape(f['description'])}</td></tr>"
        for f in item["fields"]
    )
    downloads = "".join(
        f"<a href='{escape(r['href'])}'>Download {escape(r['format'])} ({escape(r['size'])})</a> "
        for r in item["resources"]
    )
    return (
        _head(item["title"], GAMMA_CSS)
        + "<header><strong>LAKEVIEW CIVIC LAKEHOUSE</strong> · open register</header>"
        + "<main class='wrap'><p><a href='/gamma/browse'>← register</a></p><article class='record'>"
        + f"<p class='eyebrow'><span>{escape(item['category'])}</span></p>"
        + f"<h1>{escape(item['title'])}</h1>"
        + f"<p class='byline'>Published by <strong>{escape(item['publisher'])}</strong></p>"
        + f"<p>{escape(item['description'])}</p>"
        + "<dl class='record-facts'>"
        + f"<dt>Time coverage</dt><dd>{escape(item['temporal'])}</dd>"
        + f"<dt>Geography</dt><dd>{escape(item['geography'])}</dd>"
        + f"<dt>Licence</dt><dd>{escape(item['license'])}</dd>"
        + f"<dt>Update cadence</dt><dd>{escape(item['frequency'])}</dd></dl>"
        + "<h2>Columns</h2><table class='schema-grid'><thead><tr><th>Column</th><th>Data type</th>"
        + f"<th>Notes</th></tr></thead><tbody>{rows}</tbody></table>"
        + f"<p class='downloads'>{downloads}</p></article></main></body></html>"
    )
