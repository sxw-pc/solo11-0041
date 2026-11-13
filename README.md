> **合规声明**
>
> 本项目仅采集仓库内置的 mock 公开数据门户（`127.0.0.1` / `localhost`）。配置与 fixture 中不包含真实公司名、真实域名、密钥或访问凭据。若将本项目用于任何真实站点，使用者必须事先获得授权，并自行遵守目标站点的 `robots.txt`、服务条款、速率限制与数据许可；作者不对未经授权的使用承担责任。

# 采谱仪 · datasheet-probe

**一句话定位：** 一条离线优先的公开数据集目录元数据采集与谱系档案管道，完成分页发现、详情抽取、原始留档、归一、字段校验、跨源合并、SQLite 入库和 JSON / Markdown / 统计导出。

本项目是 Coding Agent 评测用的完整素材源：自带三个风格迥异的 Flask mock 门户、22 页静态离线 fixture、可重放的原始 HTML 档案，以及覆盖全链路的 pytest 测试。它刻意不同于通用爬虫、表单填写器、链接检查器、页面变更监测器或静态表单归档项目。

## 合规与安全边界

- **运行期只访问本机回环地址。** `Fetcher` 同时校验配置允许的主机与 IP 回环属性；重定向逐跳校验，外站重定向会在发起请求前被拒绝。
- **不携带凭据。** 目标 URL 不允许包含用户名或密码，不读取登录态，不实现鉴权、验证码或任何访问控制绕过。
- **只存元数据。** 资源链接仅作为文本记录，绝不下载 CSV、ZIP、PDF 等二进制内容。
- **API 无鉴权，因此只应监听回环地址。** Flask 控制 API 带有请求来源守卫，`serve` 固定绑定 `127.0.0.1`。不要将其暴露到公网。
- **mock 名称均为虚构。** `Atlas Open Data`、`Harbor Data Exchange`、`Lakeview Civic Lakehouse` 仅用于演示，不对应真实机构或站点。

明确不实现：真实站点适配、JS 渲染、浏览器二进制、分布式任务调度、Web 人工复核 UI、站点变化监测、二进制资源下载、数据内容质量剖析。

## 三个 mock 门户

所有门户由同一个 Flask 应用提供，但视觉与 DOM 结构刻意不同，用于验证配置驱动抽取的泛化能力：

| 来源 | 风格与结构 | 分页 | 数据量 | 抽取特点 |
| --- | --- | --- | --- | --- |
| `alpha` · Atlas Open Data | 明亮卡片式目录，彩色标签，语义化 `<dl>` 与字段表格 | 查询参数 `?page=N`，2 页 | 5 个数据集 | 首选结构化 CSS selector，字段表格规则完整 |
| `beta` · Harbor Data Exchange | 密集的政务表格目录，等宽字体，键值行与 schema 表 | 查询参数 `?page=N`，2 页 | 5 个数据集 | 全部依赖 selector，展示表格型详情页的解析 |
| `gamma` · Lakeview Civic Lakehouse | 文档式衬线排版，article / `<dt><dd>` 结构 | “下一页”链接，2 页 | 6 个数据集 | 配置故意省略部分 selector，触发 `<dt>/<dd>` 与资源链接启发式回退 |

列表页共 6 页、详情页共 16 页，一次全量运行产生 **22 个原始 HTML 留档页面**。三源合计 16 个来源实例，跨源合并后得到 **15 个数据集**，其中 1 个是跨源合并数据集。

运行后可直接浏览：

- `http://127.0.0.1:5000/`：mock 门户索引
- `/alpha/catalog`、`/beta/browse`、`/gamma/browse`：三套目录
- `/health`：健康检查
- `/datasets`、`/runs/<run_id>`、`/export`：本地控制 API

## 快速开始

环境：Python 3.10+。依赖只有 `requests`、`beautifulsoup4`、`PyYAML`、`Flask`、`pytest`；SQLite 使用标准库 `sqlite3`。

```bash
python -m venv .venv
# Windows Git Bash
source .venv/Scripts/activate
# Linux / macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 1. 启动 mock 门户与 API

```bash
python -m app serve
```

服务固定监听 `http://127.0.0.1:5000`，同时提供 mock 门户和本地控制 API。另开终端验证：

```bash
curl http://127.0.0.1:5000/health
# {"mode":"offline-mock","service":"datasheet-probe","status":"ok"}
```

### 2. 运行全量采集

```bash
python -m app run --source all
```

若 `127.0.0.1:5000` 尚未启动，CLI 会自动拉起内嵌 mock 门户，运行结束后关闭；若已经 `serve`，则复用现有服务。全新数据库的典型输出：

```json
{
  "run_id": "run_20261113T020146Z_daa99b",
  "counts": {
    "new": 15,
    "updated": 0,
    "unchanged": 0,
    "merged": 1,
    "datasets": 15,
    "instances": 16,
    "findings": 13,
    "transforms": 184
  },
  "raw_pages": 22,
  "errors": [],
  "export_dir": "data/exports/run_20261113T020146Z_daa99b"
}
summary: new=15 updated=0 unchanged=0 merged=1
```

再次执行同一条命令：

```text
summary: new=0 updated=0 unchanged=15 merged=1
raw_pages=22
errors=[]
```

`new / updated / unchanged` 是数据集级计数，三者之和等于当前数据集总数；`merged` 表示当前有多个来源实例的数据集数量，与前三个计数存在重叠，不是第四类互斥状态。

一次全量运行中可观察到的阶段结果：

| 来源 | 列表页 | 发现详情 | 抽取实例 | crawl_run 状态 |
| --- | ---: | ---: | ---: | --- |
| `alpha` | 2 | 5 | 5 | `ok` |
| `beta` | 2 | 5 | 5 | `ok` |
| `gamma` | 2 | 6 | 6 | `ok` |

```text
data/raw/<run_id>/                22 个 HTML + manifest.json
data/datasheet_probe.db           crawl_run / dataset / dataset_instance / field / resource /
                                  raw_archive / field_transform / validation_finding / merge_decisions
data/exports/<run_id>/
  archive.json
  archive.md
  stats.json
```

`archive.md` 的关键结构（节选）：

```markdown
# 数据集谱系档案

## 概览
| 数据集 | 15 |
| 来源实例 | 16 |
| 合并数据集 | 1 |

## 告警摘要
| 错误 | 2 |
| 告警 | 10 |
| 信息 | 1 |

## environment（5）

### Air Quality Monitoring Stations 2023
- `dataset_uid`: `ds_...`
- 类别 / 发布方: **environment** / Atlas Environmental Agency

**字段（4）**
- `station_id` (string): Stable station identifier

**资源（2）**
- CSV · 12.4 MB (13002342 bytes) · `http://127.0.0.1:5000/alpha/files/air-quality-2023.csv`

**来源溯源**
- `alpha` — http://127.0.0.1:5000/alpha/dataset/air-quality-2023
  留档: data/raw/<run_id>/<sha1(url)>.html · sha256 ...
- `beta` — http://127.0.0.1:5000/beta/record/hde-001
  留档: data/raw/<run_id>/<sha1(url)>.html · sha256 ...

**告警（1）**
- **INFO** `cross_source.field_count_gap` · `fields` · __cross_source__ — ...

**跨源合并**: exact_title+publisher+period · score=1.0 · manual_override=0
```

### 3. 完全离线重放

静态 fixture 覆盖三套门户的分页页与详情页，重放路径不会发起任何 HTTP 请求：

```bash
python -m app run --source all \
  --replay-from fixtures \
  --db data/replay.db \
  --data-dir data/replay
```

也可以从某次运行留下的 raw 目录重放：

```bash
python -m app run --source all \
  --replay-from data/raw/<run_id> \
  --db data/replay.db \
  --data-dir data/replay
```

每次 HTTP 抓取的 HTML 保存在 `data/raw/<run_id>/`，同目录 `manifest.json` 记录 URL、状态码、内容类型、字节数和 sha256。重放时会重新校验 fixture / raw 文件的 sha256，并把留档登记到新的 `crawl_run` 中。`fixtures/` 可通过 `python tools/build_fixtures.py` 重新生成。

### 4. 导出与查询

```bash
# Markdown；同时总会写出 archive.json 与 stats.json
python -m app export --format md --out data/exports/manual

# 仅导出某次 run 的数据集
python -m app export --format json --run-id <run_id>

# 只读查询
python -m app inspect --category environment --severity warn --limit 20
```

管道默认在 `data/exports/<run_id>/` 写出：

- `archive.json`：完整档案，含统计、字段、资源、来源实例、抽取痕迹、校验发现、合并决策和原始留档溯源。
- `archive.md`：按类别分组的人工可读档案，含告警摘要、字段、资源、`**来源溯源**` 与合并信息。
- `stats.json`：与 JSON 档案中的 `stats` 完全一致。

### 5. 测试

```bash
pytest -q
```

测试启动临时的本地 Flask 服务，全部访问 `127.0.0.1`。离线端到端测试会 monkeypatch 掉 `requests` 的 HTTP 方法，证明 replay 路径不会触网。

## 配置说明

### `config/sources.yaml`

三个来源共享 `defaults`，再分别声明分页、列表与详情选择器：

```yaml
defaults:
  timeout_seconds: 3
  max_retries: 1
  min_interval_seconds: 0.02
  max_response_bytes: 2097152
  allowed_hosts: ["127.0.0.1", "localhost", "::1"]

sources:
  gamma:
    base_url: "http://127.0.0.1:5000"
    start_path: "/gamma/browse"
    min_interval_seconds: 0.04   # 可覆盖 defaults 的请求间隔下界
    pagination:
      type: next_link          # query / next_link / none
      next_selector: "a.next-page"
      max_pages: 3             # 代码中仍硬限制为 50
    list:
      item_selector: "article.dataset-brief"
      link_selector: "h2 a"
    detail:
      selectors:
        title: "article.record h1"
        category: "p.eyebrow span"
        publisher: "p.byline strong"
      # 未声明的字段由启发式回退抽取
```

- `base_url` / `start_path`：目录起点。查询式分页从 `start` 开始，按 `step` 递增；链接式分页跟随 `next_selector`。
- `list.item_selector` / `link_selector`：列表项与详情链接；`item_id_regex` 可指定详情 ID。
- `detail.selectors`：标量元数据选择器。缺失时依次尝试 `<dt>/<dd>`、键值表、`h1` 等启发式路径。
- `detail.field_table`：字段表选择器；失败时根据表头关键字识别字段名、类型和描述列。
- `detail.resources`：资源列表选择器；失败时扫描下载型链接并推断格式与大小。
- `defaults.allowed_hosts`：额外 host 白名单，仍必须通过 IP 回环检查；`trust_env=False` 禁用环境代理和 netrc。
- 每次抽取都会记录 selector、DOM 路径、文本片段和 `fallback_used`；原始 HTML 与抽取 JSON 都会进入档案。

### `config/normalize_rules.yaml`

- `categories`、`frequency`、`formats`、`field_types`、`geography`、`license_aliases`：同义词到规范值的映射。
- `size_units`：KB/MB/GiB 等向字节换算；未知或非数字大小保留原文并产生告警。
- `dedupe.title_similarity_threshold`：跨源标题相似度阈值（默认 `0.86`）。
- `dedupe.require_same_publisher / period / geography`：合并前的硬约束。
- `dedupe.field_priority`：越靠前优先级越高（`alpha > beta > gamma`），用于标量字段补齐与主来源选择。
- `validation.required`：缺失即产生 `error`；枚举不合法、时间倒置、未知字段类型、非数字大小等产生 `warn`；跨源字段数差距产生非阻断 `info`。

## 管道输出与 SQLite 表

阶段数据流详见 [`docs/architecture.md`](docs/architecture.md)：发现 → 抓取留档 → 抽取 → 归一 → 校验 → 合并 → 入库 → 导出。

| 表 | 作用 |
| --- | --- |
| `crawl_run` | 每次 source 运行的阶段、状态、页数、发现数、耗时与错误 |
| `dataset` | 合并后的规范数据集主表，含内容指纹与首末 run |
| `dataset_instance` | 每个来源 URL 的实例快照与逐字段抽取 JSON |
| `field` | 合并数据集字段定义与 selector / DOM 位置溯源 |
| `resource` | 资源格式、字节大小、原始大小文本、URL 与位置 |
| `raw_archive` | 每个列表/详情页的 raw 文件路径、状态码、sha256 与抓取时间 |
| `field_transform` | 归一化前后值、规则名、实例与状态 |
| `validation_finding` | `error / warn / info` 级校验发现与证据 |
| `merge_decisions` | 合并成员、依据、相似度分数、人工覆盖标记的保留位置 |

写入使用确定性的 `dataset_uid`、实例 ID 和内容指纹。重复运行时，数据集级计数区分 `new / updated / unchanged / merged`；字段、资源、转换、校验发现和合并决策同样使用 upsert 或按数据集替换，不会重复堆积。`raw_archive` 按 `(run_id, url, kind)` 唯一，因此重复运行会增加新的审计留档，但不会污染数据集计数。

## 本地控制 API

| 方法与路径 | 说明 |
| --- | --- |
| `GET /health` | 服务状态 |
| `GET /` | mock 门户索引 |
| `POST /runs` | 触发采集，body 如 `{"source": "all"}`；可选 `replay_from` |
| `GET /runs/<run_id>` | 运行详情与数据集 / 留档 / 发现计数 |
| `GET /datasets` | 过滤查询：`category`、`publisher`、`temporal_from`、`temporal_to`、`updated_from`、`updated_to`、`severity`、`limit` |
| `GET /export?format=json\|md&run_id=...` | 即时导出，不落数据库 |

```bash
curl -X POST http://127.0.0.1:5000/runs \
  -H 'Content-Type: application/json' \
  -d '{"source": "gamma"}'

curl 'http://127.0.0.1:5000/datasets?category=environment&severity=warn'
curl 'http://127.0.0.1:5000/datasets?updated_from=2026-01-01T00:00:00%2B00:00'
curl 'http://127.0.0.1:5000/export?format=md'
```

`updated_from` / `updated_to` 比较 `dataset.updated_at`，即该数据集最近一次被管道看到的时间（即使内容未变也会刷新）；`temporal_from` / `temporal_to` 比较数据集覆盖的时间范围，两者含义不同。

## 测试覆盖

- `test_fetcher.py`：非回环与凭据 URL 拒绝、外站重定向在第二跳前拦截。
- `test_discover.py`：查询式分页、链接式分页、URL 去重、页数上限、空页与循环终止。
- `test_extract.py`：结构化 selector、Gamma 键值/资源回退、字段表启发式回退。
- `test_normalize.py`：类别/频率/许可、时间范围、地理、大小单位、格式、字段类型与全局转换。
- `test_validate.py`：必填缺失、枚举、时间倒置、各类告警、跨源 `info`。
- `test_dedupe.py`：精确/模糊合并、发布方/时段差异不合并、同源不合并、优先级。
- `test_store.py`：四项幂等计数、行数、更新行为与查询过滤。
- `test_export.py`：JSON / Markdown / stats 完整性、必填字段、溯源与分组。
- `test_e2e_pipeline.py`：本地 HTTP 全量管道、raw sha256、run 记录、二次运行幂等。
- `test_e2e_offline.py`：fixture replay 全链路，断言全程无 HTTP，并验证二次重放幂等。
- `test_api.py`：健康检查、mock 页面、控制 API 触发与查询、Markdown 导出。

## 目录结构

```text
app/
  __main__.py       CLI：serve / run / export / inspect
  server.py         Flask 应用工厂与回环请求守卫
  api.py            本地控制 API
  mock_portal.py    三套风格迥异的 mock 门户
  mock_content.py   Alpha / Beta 数据
  mock_gamma.py     Gamma 数据
  fetcher.py        回环校验、限速、重试、大小上限、raw 留档
  discover.py       配置驱动分页发现
  extract.py        selector 抽取与启发式回退
  normalize.py      规范化与字段转换审计
  validate.py       字段级校验与告警分级
  dedupe.py         跨源相似度归并
  store.py          SQLite 幂等写入
  repository.py     只读查询与档案快照
  export.py         JSON / Markdown / stats 导出
  schema.py         九张表与索引
  pipeline.py       阶段编排与 replay
config/
  sources.yaml
  normalize_rules.yaml
fixtures/           三套门户共 22 页静态 HTML + manifest
tests/              单元、集成、API 与离线端到端测试
tools/build_fixtures.py
docs/architecture.md
```

## 已知限制与后续方向

- 只内置 mock 站点；真实站点适配需要自行完成授权、robots 与条款评估。
- 不做 JS 渲染；依赖服务端返回的 HTML。动态站点需先获得合规的静态快照。
- 启发式抽取可能误判；每一条字段/资源都保留 selector、位置和片段，校验发现会显式暴露回退与非规范值。
- 跨源字段清单按优先级来源选取，标量元数据逐字段补齐；更细粒度的字段按名称融合、冲突评分和人工覆盖可作为下一迭代。
- SQLite 适合本地单机档案；并发写入和大规模历史查询需要外部数据库或分区策略。
- 目前没有 Web 复核 UI、增量调度、站点变化监测或资源内容剖析。
- 内容指纹覆盖合并身份、规范标量、字段、资源与成员集合；仍未覆盖抽取片段本身，细微 DOM 变化可能不触发 `updated`。

后续可扩展：DCAT / OAI-PMH 适配器、字段级冲突可视化、人工 override API、增量抓取窗口、对象存储 raw 后端、按数据集许可的导出策略。
