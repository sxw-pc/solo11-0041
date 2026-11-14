# 采谱仪架构说明

## 1. 定位

采谱仪是一条**可离线复现**的数据集目录元数据管道。它不追求通用爬取能力，而是把“目录页 → 详情页 → 规范化元数据 → 可追溯档案”的全过程拆成可测试、可审计的阶段：

- 输入：`config/sources.yaml` 中的目录起点、分页规则与选择器。
- 中间产物：原始 HTML、抽取 JSON、归一化转换记录、校验发现。
- 输出：九张 SQLite 表、完整 JSON 档案、Markdown 档案与统计快照。
- 重放输入：`data/raw/<run_id>/` 或 `fixtures/` 中的 HTML + `manifest.json`。

设计优先级依次是：**合规边界 → 可复现性 → 可追溯性 → 抽取泛化 → 幂等写入**。速度、并发和站点覆盖率不是本项目目标。

## 2. 模块边界

```text
                    ┌─────────────────────────────┐
                    │ config/sources.yaml         │
                    │ config/normalize_rules.yaml │
                    └──────────────┬──────────────┘
                                   │
┌──────────────┐   候选详情 URL   ┌─▼──────────────┐   RawPage + 文件  ┌──────────────┐
│ discover.py  │ ───────────────▶ │ fetcher.py     │ ────────────────▶ │ raw_archive  │
│ 分页 / 去重  │                  │ 回环 / 限速/重试│                   │ + manifest   │
└──────┬───────┘                  └────────────────┘                   └──────────────┘
       │                                    │ HTML
       │                                    ▼
       │                           ┌──────────────────┐
       │                           │ extract.py       │ selector + 回退
       │                           │ 抽取字段/资源/痕迹│
       │                           └────────┬─────────┘
       │                                    │ record
       │                                    ▼
       │                           ┌──────────────────┐
       │                           │ normalize.py     │ 规范值 + transforms
       │                           └────────┬─────────┘
       │                                    │ instance
       │                                    ▼
       │                           ┌──────────────────┐
       │                           │ validate.py      │ error / warn / info
       │                           └────────┬─────────┘
       │                                    │ findings
       │                                    ▼
       │                           ┌──────────────────┐
       │                           │ dedupe.py        │ 跨源聚类 + 字段优先级
       │                           └────────┬─────────┘
       │                                    │ dataset groups
       │                                    ▼
       │                           ┌──────────────────┐
       │                           │ store.py         │ 幂等 upsert + 审计
       │                           └────────┬─────────┘
       │                                    │ SQLite
       │                                    ▼
       │                           ┌──────────────────┐
       │                           │ repository.py    │ 只读快照
       │                           └────────┬─────────┘
       │                                    │
       │                                    ▼
       │                           ┌──────────────────┐
       │                           │ export.py        │ JSON / MD / stats
       │                           └──────────────────┘
       │
       └──────────── replay_from ────────────┐
                                             ▼
                                  fixtures/ 或 data/raw/ 的静态 HTML
                                  （不创建 Fetcher，不发起 HTTP）
```

`pipeline.py` 只负责阶段编排，不承载抽取、校验或 SQL 细节；`api.py` 和 `__main__.py` 是两个入口，共用同一条 `run_pipeline`。`mock_portal.py` 只提供本地被采集目标，不包含任何真实站点内容。

> 实现顺序说明：响应字节在抓取返回时立即写入 `data/raw/`，因此实际顺序是「发现 → 抓取/留档 → 抽取」。这保证即使后续解析失败，原始证据仍然存在；逻辑上的“留档”与 F3 对应。

## 3. 分阶段数据流

### 阶段 0：配置装载

**模块：** `pipeline.load_sources`、`normalize.load_rules`

- 读取 `sources.yaml`，合并 `defaults` 与各 source 配置。
- 读取 `normalize_rules.yaml`，构建同义词索引。
- `selected_source_names` 将 `all`、单源或逗号分隔源名解析为固定顺序；未知源立即报错。
- `--source` 一次只选择配置中存在的源；数据源共享同一回环 netloc 时 CLI 才自动启动内嵌 mock。

**失败策略：** 配置缺失、YAML 语法错误、未知源名直接终止运行；不产生半次 run 的数据集写入。

### 阶段 1：分页发现

**模块：** `discover.discover_source`

输入目录起点，输出 `DiscoveryResult`：

```text
page_count          已抓取的列表页数量
discovered          去重后的候选详情页数量
entries[]           {url, item_id, source}
pages[]             RawPage 对象（供留档）
```

支持三种分页：

| 类型 | 行为 |
| --- | --- |
| `query` | 复制起始 URL，移除同名参数，按 `start + step` 递增并 URL 编码 |
| `next_link` | 每页选择 `next_selector` / `list.next_selector`，只解析相对或绝对 href |
| `none` | 仅抓起始页 |

硬约束：

- `max_pages` 在代码中钳制到 `1..50`，配置无法无限翻页。
- 列表页 URL 与详情页 URL 分别去重。
- 当前页没有新详情项时停止；出现重复页 URL 时停止，避免“下一页”循环。
- 只跟随 href，不执行 JavaScript；“下一页”不存在即自然结束。
- 每个列表页仍通过 `Fetcher.fetch(..., kind="list")`，因此也受回环、限速、大小和留档约束。

**失败策略：** 发现阶段的异常被 source 级 try/except 捕获，`crawl_run.status=error`，其余 source 继续。成功但部分详情失败时为 `partial`。

### 阶段 2：抓取与原始留档

**模块：** `fetcher.Fetcher`

这是唯一的 HTTP 出口。每次请求前后都执行以下约束：

1. scheme 只能是 `http` / `https`。
2. URL 不能带用户名或密码。
3. hostname 必须同时满足 `defaults.allowed_hosts` 与 `ipaddress.ip_address(host).is_loopback`；`localhost` 显式放行。
4. `Session.trust_env = False`，禁用环境代理与 netrc。
5. 请求设置 connect/read timeout、用户代理、`Accept`。
6. 响应支持流式读取；累计字节数超过 `max_response_bytes` 立即中止。
7. 5xx 最多重试 `max_retries` 次；4xx 会先留档再抛错。
8. 重定向不交给 requests 自动跟随，而是逐跳读取 `Location`，**先校验下一跳仍是回环地址再请求**，最多 3 跳。
9. 同一 Fetcher 的请求之间遵守 `min_interval_seconds`；每个 source 可在 `sources.yaml` 中覆盖自己的请求间隔下界。

每个响应生成：

```text
data/raw/<run_id>/<stable_id>.html      原始字节，sha256 基于响应体
data/raw/<run_id>/manifest.json         所有条目的 URL / final_url / item_id / stable_id / kind /
                                        status / bytes / sha256 / fetched_at
raw_archive 表                          同样的审计字段 + 文件路径
```

`stable_id` 由 `source`、`kind` 与 `item_id` 派生；详情页的 `item_id` 取跟随重定向后最终 URL 的路径末段，列表页没有 `item_id` 时回退到 URL 指纹。因此两个入口 URL 经 302/307 收敛到同一详情页时，只会产生一个落地文件、一条 manifest 条目和一行 `raw_archive`，`_provenance` 也能按同一稳定键唯一命中。

文件写入后生成 `RawPage`，其 `html` 字段供后续解析；资源二进制链接仅被记录为字符串，不会请求。

**失败策略：** 单个详情页异常只追加到该 source 的 `source_errors`，不终止整个数据源；列表页抓取失败则终止该 source 的发现。所有错误写入 `crawl_run.error`（最多截取前 5 条）并返回给 CLI/API。

### 阶段 3：详情抽取

**模块：** `extract.extract_detail`

抽取顺序：

1. **结构化 selector：** 读取 `detail.selectors`，取文本或属性。
2. **键值回退：** 当 selector 未命中或未配置，扫描 `<dt>/<dd>`、两列表格、以冒号结尾的文本节点，并用 `FIELD_ALIASES` 做中英文键名匹配。
3. **标题回退：** 使用 `<h1>` / document title。
4. **字段表回退：** 配置 selector 失败后，扫描表头含 field/name/type/description（及中文别名）的表格；识别不到类型时保留原值，交由校验产生告警。
5. **资源回退：** 配置 selector 失败后，扫描下载型 `<a>`，从扩展名、`data-format`、链接文本推断格式，从相邻文本提取 `12.4 MB` / `18,432,000 bytes` 等大小。

每条 record 附带：

```json
{
  "title": "...",
  "category": "...",
  "publisher": "...",
  "temporal": "...",
  "geography": "...",
  "license": "...",
  "update_frequency": "...",
  "fields": [{"name": "...", "type": "...", "description": "...",
              "source_selector": "...", "location": "..."}],
  "resources": [{"format": "...", "size_raw": "...", "url": "...",
                 "source_selector": "...", "location": "..."}],
  "extraction": {
    "fallback_used": true,
    "fields": {"temporal": {"selector": "...", "fallback": true,
                            "location": "...", "snippet": "..."}},
    "lists": {"resources": {"fallback": true, "location": "..."}}
  }
}
```

`location` 是简化的 DOM 路径，`snippet` 最长 160 字符。这样即使抽取误判，也能从档案定位到原始页面节点。

**失败策略：** 解析异常按详情页记入 source 错误。缺少具体字段不抛异常，由下一阶段生成字段级 finding。

### 阶段 4：归一化

**模块：** `normalize.normalize_record`

| 字段 | 规范化 | 示例 |
| --- | --- | --- |
| category / frequency / license | 配置同义词表，宽松键匹配大小写、斜杠和连字符 | `Environmental monitoring` → `environment` |
| format / field type | 别名表 + 扩展名 | `Float` → `number` |
| geography | 规范地名表；支持国家与州 | `United States` → `US`；`Washington` → `US-WA` |
| size | 数字 + 单位换算，保留 `size_raw` | `18,432,000 bytes` / `4.5 MB` → 字节 |
| temporal | 支持 `年`、`年-月`、`年-月-日`、`/`、`to`、`–` 分隔；单值按整年展开 | `2023` → `2023-01-01` / `2023-12-31` |
| title | NFKC、casefold、去标点、压缩空白，用于相似度 | `Air Quality (2023)` → `air quality 2023` |

每一次实际变化生成 `field_transform`：

```text
{field_name, rule, before_value, after_value, status}
```

`status=applied` 表示完成映射；无法识别时保留原值并等待校验告警。归一化不静默丢弃原值。

**失败策略：** 无法解析的时间保留原文本，`temporal_start` 为空，由必填校验产生 `error`；无法解析的大小保留原文，由校验产生 `warn`。

### 阶段 5：校验与告警分级

**模块：** `validate.validate_record`、`validate.validate_cross_source`

校验不阻断数据入库，只产生 finding：

| 级别 | 规则示例 | 是否阻断 |
| --- | --- | --- |
| `error` | 必填字段缺失、类别不在枚举、更新频率不在枚举、时间起点晚于终点 | 否 |
| `warn` | 未知字段类型、大小非数字、资源 URL 缺失、使用启发式回退 | 否 |
| `info` | 跨源合并组内字段数量差距超过绝对值和比例阈值 | 否 |

跨源比较只针对同一合并簇内、至少两个 source 的实例。字段数量 `high - low >= absolute` 且 `(high-low)/high >= ratio` 时生成一条 `__cross_source__` finding。

**失败策略：** 校验函数只读 record，不写数据库。写入阶段以数据集为单位替换 findings，防止陈旧告警残留。

### 阶段 6：跨源去重与合并

**模块：** `dedupe.group_instances`

同一 source 的实例永不合并；跨源实例按以下硬条件筛选：

1. 双方都有 source，且 source 不同。
2. 发布方规范化后一致（可由 `require_same_publisher` 关闭）。
3. 时间范围完全一致且非空（可由 `require_same_period` 关闭）。
4. 地理范围一致且非空（可由 `require_same_geography` 关闭）。

满足硬条件后比较规范化标题：

- 完全相等 → `score=1.0`，依据 `exact_title+publisher+period`。
- 不等 → `difflib.SequenceMatcher.ratio()`；低于可配置阈值则不可合并。
- 通过阈值 → 依据 `fuzzy_title+publisher+period`。

合并组：

- 成员按 `dedupe.field_priority` 排序，排在前面的 source 作为主来源。
- 标量元数据逐字段取最高优先级来源的非空值。
- 字段和资源清单取最高优先级来源的非空清单；若该来源为空，则由下一优先级补全。
- 成员集合、主来源、分数、依据写入 `merge_decisions`；重复运行时保留 `manual_override` 字段，不覆盖人工决策位。

**标识生成：**

```text
instance_id = "inst_" + sha1(source + "|" + source_url)[:16]
dataset_uid = "ds_"   + sha1(title_norm|publisher_norm|period[|source|url])[:16]
```

`publisher` 为空时追加主来源与 URL，避免不同空发布方记录冲突。同源重复标题导致 UID 冲突时，追加首个 `instance_id` 再哈希，保证跨次运行稳定。

**失败策略：** 发布方、时段或地理缺失且配置要求一致时不合并，各自保留为独立数据集；字段或资源清单为空时由下一优先级来源补全。UID 冲突只改变冲突组的后缀哈希，不影响正常组；抽取上游异常不会进入本阶段。

### 阶段 7：SQLite 幂等写入

**模块：** `store.Store`、`schema.SCHEMA_SQL`

`persist_groups` 对每个合并组：

1. 按 `dataset_uid` 查询已有 `fingerprint`。
2. 不存在 → 插入，`new += 1`。
3. 存在但 fingerprint 不同 → 更新并刷新 `last_run_id`，`updated += 1`。
4. 存在且 fingerprint 相同 → 只刷新 `last_run_id`，`unchanged += 1`。
5. 若组内 source 数 > 1，`merged += 1`（与前三项重叠，不是互斥状态）。

数据集 fingerprint 覆盖合并身份、类别、地理、许可、频率、字段、资源、成员实例和来源集合。因此字段、资源或成员变化会触发 `updated`，而纯抓取时间的差异不会。

子表策略：

- `dataset_instance` 按 `instance_id` upsert，同一来源 URL 不重复。
- `field` / `resource` 使用 `(dataset_uid, ordinal)` 稳定 ID upsert，并删除当前数据集中已不存在的旧序号，避免缩短列表后残留。
- `field_transform` 按 `(instance_id, field_name, rule)` upsert，并清除当前数据集不再产生的转换。
- `validation_finding` 以数据集为单位先删后写，保证 findings 与本次规范状态一致。
- `merge_decisions` 按 `dataset_uid` upsert；若数据集不再跨源，删除旧决策；更新语句不触碰 `manual_override`。
- `raw_archive` 唯一键是 `(run_id, stable_id)`，`stable_id` 由 `(source, kind, item_id)` 派生。同一 run 内同一逻辑页面的重复留档（例如两个入口 URL 重定向到同一详情页）会 upsert 同一行；不同 run 仍各留一行，这是有意的：每次运行都应有自己的留档和 sha256。

事务：每个 `persist_groups` 调用使用一个 SQLite 上下文事务；`record_run`、`archive_raw` 在抓取过程中即时提交，确保运行失败后仍保留审计。

**失败策略：** SQLite 写入异常回滚当前 `persist_groups` 事务并向上传播；抓取审计已先行提交，因此不会丢失 raw 证据。CLI 将异常转为非零退出码，不打印虚假的四项计数。

### 阶段 8：导出与本地 API

**模块：** `repository.snapshot`、`export.export_archive`、`api.py`

快照按 `run_id` 过滤数据集（未指定则全库），聚合：

- 数据集数、来源实例数、合并数据集数、字段数、资源数、raw 页面数。
- 类别、发布方、严重级别分布。
- 每个数据集的字段、资源、来源实例、抽取 JSON、findings、合并决策和 raw provenance。

provenance 优先用 `(source, kind='detail', source_item_id)` 派生的 `stable_id` 命中当前 `run_id` 的 `raw_archive`；没有 `item_id` 或旧库缺少稳定键时才回退到 URL。手工导出未指定 run 时取该稳定键最新的一条。这样重放导出的溯源不会误指向后续 HTTP 运行，两个入口 URL 重定向到同一详情页时也能命中同一行留档。

导出目录：

```text
data/exports/<run_id>/
  archive.json      schema_version / generated_at / run_id / stats / datasets
  archive.md        概览、告警摘要、按类别分组、字段、资源、来源溯源、合并
  stats.json        与 archive.stats 相同
```

API 端点直接调用同一快照和管道函数，不维护另一套业务逻辑。`GET /datasets` 支持按类别、发布方、覆盖时间窗、最近更新时间窗（`dataset.updated_at`）和告警级别过滤；`GET /export` 支持按 run 即时导出。`POST /runs` 的 base URL 取自当前请求的 host，仍受 Fetcher 回环校验；Flask `before_request` 拒绝非 `127.0.0.1` / `::1` 的远程地址。

**失败策略：** 不存在的 run 返回 404，非法查询参数返回 400；管道或文件写入异常由 Flask 返回 500，不会返回伪造的导出正文。导出只读 SQLite，不修改档案。控制 API 不做鉴权，只能用于回环本地环境。

## 4. Replay 分支

Replay 与 HTTP 分支在阶段 2 分叉，之后完全共用：

```text
manifest entries
  ├─ 校验文件存在
  ├─ 计算实际 sha256，与 manifest 不一致则记错误并跳过
  ├─ 写入 raw_archive（kind=list/detail）
  ├─ list 条目只计入页数，不解析
  └─ detail 条目读取 UTF-8 HTML → extract → normalize → validate → dedupe → store → export
```

特点：

- `run_pipeline(replay_from=...)` 不实例化 `Fetcher`；`requests` 完全不在调用链上。
- 可以重放 `fixtures/`，也可以重放某次 `data/raw/<run_id>/`。
- 重放时仍生成新的 `run_id` 和 `crawl_run` 记录，因此不会覆盖历史审计。
- 重复重放同一 manifest，数据集计数保持 `new=0 / updated=0 / unchanged=15`。
- 若指向 `data/raw/` 根目录，会合并多个 run 的 manifest；normalize 阶段会按 `instance_id` 去重，避免同一详情重复进入合并。生产式用法应指向具体 run 目录。

## 5. 失败处理总表

| 失败点 | 处理 | 影响范围 |
| --- | --- | --- |
| YAML 缺失/语法错误 | 异常向上传播，CLI 非零退出 | 整次运行 |
| 未知 source | `ValueError` | 整次运行 |
| 列表页网络失败 | source status=error，继续下一 source | 当前 source |
| 单个详情页失败 | 收集错误，source status=partial | 单个详情 |
| 非回环/带凭据/超限 URL | `FetchError`，请求不发出 | 当前 URL |
| 5xx | 最多重试配置次数 | 当前 URL |
| 4xx | 先留档后报错 | 当前 URL |
| 外站重定向 | 在下一跳请求前拒绝 | 当前 URL |
| selector 未命中 | 启发式回退 + `fallback_used` + `warn` | 字段/资源 |
| 字段缺失或格式非法 | finding，不阻断入库 | 字段级 |
| 无任何数据集 | 返回零计数；CLI 退出码 2 | 整次运行 |
| replay 文件缺失/sha 不符 | source 错误，跳过该条目 | 单条目 |
| SQLite 写入异常 | 事务回滚并向上抛出 | 当前 persist 调用 |

## 6. 测试策略与可复现性

- **单元隔离：** discover 使用 fake fetch；extract 直接读 fixture HTML；normalize/validate/dedupe 使用纯函数输入。
- **临时集成：** `conftest.py` 用 `werkzeug.make_server` 在 `127.0.0.1` 的临时端口启动 Flask，端口不写死。
- **端到端 HTTP：** 全量运行，校验 raw 文件 sha256、crawl_run 状态、数据库行数和二次运行幂等。
- **端到端离线：** monkeypatch `requests.Session.get` 与 `requests.get` 为抛错函数，再重放 fixture，从机制上证明无 HTTP。
- **时间可复现：** fixture manifest 固定抓取时间和 sha256；测试不依赖网络时钟。运行 ID 使用 UTC 时间 + 随机后缀，只用于区分运行目录。

## 7. 为什么不是通用爬虫

本项目的关键产物是**谱系档案**，不是页面数量：每个规范字段都能回溯到来源实例、selector、DOM 位置和 raw 文件；每次归一化都有前后值；每个合并都有可配置依据；每次写入都区分新增、更新、未变并保留历史 raw。通用抓取能力、分布式调度、登录态维持和反爬绕过均不在设计范围内。
