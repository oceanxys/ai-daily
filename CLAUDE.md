# ai-daily — CLAUDE.md

## 项目概览

个人 AI 学习助手系统。每天 22:00 由 macOS launchd 自动触发，抓取 AI 资讯（O'Reilly Radar、TechCrunch）、arXiv CS.AI 论文、Reddit 热帖，通过 Claude API 总结分类后生成 8 个静态 HTML 页面；同时把论文、精选高亮、热词、文章摘要推送到 Railway PostgreSQL，供扣子 Agent 通过 HTTP API 消费。

---

## 项目结构

| 文件 / 目录 | 作用 |
|---|---|
| `fetch_news.py` | 主流程：抓取 → Claude 总结 → 生成精选高亮 → 向量化 → 生成页面 → 推送云端（启动时自动 tee stdout/stderr 到 `logs/run.log`，含 `Tee` 类、`generate_highlights`、`push_highlights_to_cloud`、`generate_embeddings`、`push_embeddings_to_cloud`）|
| `brain.py` | Agent 大脑：sense / think / act / reflect 四阶段决策循环 |
| `api.py` | Flask REST API（本地端口 5001 / Railway 云端），管理所有数据读写 |
| `run.sh` | 启动 api.py 后台进程，再运行 fetch_news.py，日志写 logs/run.log |
| `memory.db` | 本地 SQLite：文章去重、话题追踪、分类偏好 EMA 权重（不上云）|
| `output/` | 生成的 8 个静态 HTML 页面（index / today / models / trending / benchmark / tools / jobs / archive）|
| `logs/` | run.log（主流程日志）、api.log（API 服务日志）|
| `data/` | papers_today.json、highlights.json（本地 JSON 备份）|
| `archive/` | 历史文章存档（按日期 JSON）|
| `DEVLOG.md` | 开发日志，每次改动后更新 |

---

## 云端配置

- **Railway 项目**：zooming-education
- **API 基础地址**：`https://web-production-6e883.up.railway.app`
- **部署方式**：push 到 GitHub `main` 分支自动触发重新部署

### PostgreSQL 表结构

| 表名 | 用途 |
|---|---|
| `papers` | arXiv 论文（title / authors / abstract / arxiv_url / published / categories）|
| `highlights` | 精选论文高亮（title / summary / reason / arxiv_url）|
| `topic_history` | 每日热词记录（keyword / heat / summary / date / count，UNIQUE keyword+date）|
| `topic_summaries` | 热词详情（keyword PK / brief / background / key_points / sources）|
| `articles_cloud` | 每日文章摘要（title / chinese_title / chinese_summary / category / source / link UNIQUE / date）|
| `embeddings` | 语义检索向量（source_type / source_id TEXT / content / vector(512) / metadata，pgvector 扩展，UNIQUE source_type+source_id）|

### API 路由速查

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/papers?limit=N&category=xxx` | 今日论文列表，支持 `limit` 限制返回条数、`category` 按 arXiv 分类过滤 |
| GET | `/highlights` | 最近 24 小时精选论文高亮 |
| GET | `/topics?range=today\|week\|month` | 热词榜，含 trend 标签 |
| GET | `/topic/<keyword>` | 热词详情 + 30 天历史 |
| GET | `/articles?date=YYYY-MM-DD&category=xxx&keyword=xxx` | 指定日期文章列表，`keyword` 在中文标题/摘要做 ILIKE 全文搜索 |
| POST | `/update_papers` | 写入论文 |
| POST | `/update_highlights` | 写入精选高亮 |
| POST | `/update_topics` | upsert 热词和简报 |
| POST | `/update_articles` | 写入文章摘要（ON CONFLICT DO NOTHING）|
| POST | `/update_embeddings` | 写入向量（pgvector，512 维）|
| GET | `/search?query=xxx&limit=N&source_type=article\|paper\|all` | 语义搜索，返回按余弦相似度降序的结果（默认 limit=5，上限 20）|
| GET | `/health` | 服务状态 + 各表今日计数 |

---

## 开发规范

### 运行本地脚本

```bash
source ~/.zshrc && python3 fetch_news.py
```

必须先 source，否则 `ANTHROPIC_API_KEY` 不在环境变量中，所有 Claude 调用都会失败。

### 修改 api.py 后

必须 push 到 GitHub，Railway 会自动重新部署（约 30 秒）。不需要手动重启。

### 本地 vs 云端数据库

| | 本地 | 云端 |
|---|---|---|
| 数据库 | SQLite（`memory.db`）| PostgreSQL（Railway）|
| 用途 | 文章去重、话题追踪、分类偏好 | 对外 API、扣子 Agent 消费 |
| 同步方式 | 不直连，通过 HTTP API 推送 | — |

**绝对不要**直连 Railway PostgreSQL 写数据，所有写入都走 `api.py` 的 POST 接口。

### 常量管理

云端 API 地址统一用 `fetch_news.py` 顶部定义的 `CLOUD_BASE` 常量，不要在函数里硬编码。

---

## 后续规划

1. **数据全量上云**：把本地 memory.db 里的文章去重记录、话题追踪、分类偏好权重也同步到 PostgreSQL，彻底摆脱对本地 SQLite 的依赖
2. **扣子多 Agent 系统**：在扣子平台搭建多 Agent 协同，消费 Railway 云端数据
   - 资讯 Agent：每日拉取 `/articles` 推送到用户
   - 论文 Agent：跟踪 `/papers` 和 `/highlights`
   - 热词 Agent：监控 `/topics` 趋势变化并预警
3. **前端展示**：考虑把 8 个静态 HTML 部署到 Vercel/Netlify，直接调用 Railway API 动态渲染

### RAG 知识库（已上线）

- PostgreSQL 启用 pgvector 扩展
- `embeddings` 表：`source_type` / `source_id TEXT` / `content` / `vector(512)` / `metadata` JSONB，`UNIQUE(source_type, source_id)`
- 嵌入模型：**Voyage `voyage-3-lite`**（512 维，索引侧 `input_type="document"`，查询侧 `input_type="query"`）
- `fetch_news.py` 每日运行后自动向量化当日文章和论文，文章用 `link`、论文用 `arxiv_url` 作为 `source_id`，推送到 `embeddings` 表
- 写入接口：`POST /update_embeddings`（ON CONFLICT DO UPDATE）
- 检索接口：`GET /search?query=xxx&limit=N&source_type=article|paper|all`，返回 `{query, count, results: [{source_type, source_id, content, metadata, similarity}]}`，按 `1 - (embedding <=> query)` 余弦相似度降序排列
- 环境变量：`VOYAGE_API_KEY` 必须同时配置在本机 `~/.zshrc`（fetch_news.py 使用）和 Railway web service Variables（api.py /search 使用）

---

## Skill 使用规范

- Debug 数据抓取、日志、API、定时任务问题时，使用 `/investigate`
- 修改 `fetch_news.py`、`api.py`、数据库、Railway API 前，先使用 `/plan-eng-review`
- 完成代码改动后，使用 `/review` 检查 API 契约、数据写入、LLM 可信边界
- 页面改动后，使用 `/qa` 或 `/browse` 验证本地 `output/` 页面
- 大功能完成后，使用 `/document-release` 更新 `CLAUDE.md`
- 涉及 API key、CORS、Railway、POST 接口时，使用 `/cso`
