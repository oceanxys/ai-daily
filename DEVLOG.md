# ai-daily 开发日志

---

## 2026-05-02

### 项目初始化

**功能：多页静态 AI 日报系统**

- `fetch_news.py`：RSS 抓取 + Claude 智能总结 + 生成 8 个静态 HTML 页面
  - 页面：首页 / 模型追踪 / 热词榜 / 竞技场 / 工具库 / 求职动态 / 今日日报 / 历史归档
  - 数据源：O'Reilly Radar、arXiv CS.AI、TechCrunch（含备用源和 fallback）
  - 容错机制：10s 超时、备用源切换、Claude 质量判断、数量不足自动补充
  - 热点检测：关键词在 3 篇以上文章出现时生成「🔥 今日焦点」区块
- `api.py`：Flask 本地 API，端口 5001
  - `GET /papers`、`POST /update_highlights`、`POST /update_papers`、`GET /health`
- `server.py`：本地开发服务器，端口 8765，访问页面时自动触发抓取（30 分钟冷却）
- SQLite 记忆系统（`memory.db`）：文章去重、话题追踪、分类偏好 EMA 权重
- macOS launchd 定时任务：每天 22:00 自动执行 `run.sh`
- `run.sh`：启动 api.py（后台）+ 运行 fetch_news.py，日志写入 `logs/run.log`

---

### arXiv 论文保存

- 新增 `save_arxiv_papers()`：抓取 arXiv CS.AI RSS，提取 title / authors / abstract / arxiv_url / published / categories
- 保存至本地 `data/papers_today.json`
- 新增 `GET /papers` 接口读取论文列表
- 新增「📚 今日精选论文」区块渲染到 today.html（从 highlights.json 读取）

---

### 项目 Git 初始化 & 部署

- 初始化 Git 仓库，配置 `.gitignore`（排除 data/、logs/、output/、memory.db 等）
- 推送至 GitHub：`github.com/oceanxys/ai-daily`
- 部署到 Railway（云端 Flask API）
  - `Procfile`：`web: python api.py`
  - `requirements.txt`：anthropic / feedparser / httpx / flask / flask-cors
  - 云端地址：`https://web-production-6e883.up.railway.app`

---

### 云端数据推送

- `fetch_news.py` 保存论文后，自动 POST 到 Railway `/update_papers`
- `api.py` 新增 `POST /update_papers` 接口
- **Bug 修复**：`api.py` 缺少 `import os` 导致 `os.environ.get("PORT")` 报错 → 已修复

---

### PostgreSQL 数据库迁移

- Railway 项目内添加 PostgreSQL 服务
- `api.py` 改为双模式：有 `DATABASE_URL` 时用 PostgreSQL，否则降级到本地 JSON 文件
- 启动时自动建表：`papers`（id / title / authors / abstract / arxiv_url / published / categories / created_at）、`highlights`（id / title / summary / reason / arxiv_url / created_at）
- 各接口行为：
  - `/update_papers`：写入 DB + 同步写本地 JSON 备份
  - `/update_highlights`：写入 DB + 同步写本地 JSON 备份
  - `/papers`：优先读 DB，fallback 读 JSON
  - `/health`：DB 模式返回 `source: "postgresql"`，文件模式返回 `source: "file"`
- `requirements.txt` 新增 `psycopg2-binary==2.9.10`
- Railway web 服务设置环境变量：`DATABASE_URL=postgresql://...@postgres.railway.internal:5432/railway`

---

### /papers 接口格式优化

- 返回格式从数组改为对象：`{"papers": [...], "count": N, "date": "YYYY-MM-DD"}`
- `authors` 字段从数组转为逗号分隔字符串：`["Alice", "Bob"]` → `"Alice, Bob"`
- 两处均在 PostgreSQL 和文件 fallback 两条路径上同步处理

---

### Agent 大脑模块

**新增 `brain.py`**，实现四阶段决策循环：

| 阶段 | 方法 | 内容 |
|------|------|------|
| 感知 | `sense()` | 查 memory.db 7 天文章趋势、热门话题、长期话题；读 papers_today.json |
| 判断 | `think(state)` | 调用 Claude，按优先级规则返回决策 JSON |
| 行动 | `act(decision)` | find_more 补抓备用源；special_report 生成专题 HTML；topic_summary 生成时间线 HTML |
| 反思 | `reflect()` | 写入 memory.db 的 `agent_log` 表 |

**决策优先级（topic_summary > special_report > find_more > normal）：**
- `topic_summary`：有话题连续追踪 ≥ 5 天
- `special_report`：热门话题连续出现 ≥ 5 天
- `find_more`：昨日文章 < 10 条
- `normal`：其他情况

`fetch_news.py` 集成：
- `save_arxiv_papers()` 之后调用 `agent.run()`
- find_more 的补充文章在去重前合并
- today.html 顶部新增「🧠 今日 Agent 决策」区块（显示决策类型、理由、执行结果）
- 结尾调用 `agent.reflect()` 记录结果

---

### Bug 修复记录

| 时间 | Bug | 原因 | 修复方式 |
|------|-----|------|---------|
| 05-02 | Agent think() JSON 解析失败 | Claude 在 `priority_categories` 数组中输出裸中文词（如 `[科技, 财经]`），非法 JSON | 从 prompt 中删除该字段，只保留 action / reason / targets |
| 05-02 | Agent 失败导致主流程中断 | `Agent()` 抛异常时 `agent_decision` 变量未定义 | 用 try/except 包裹，预设安全默认值；`reflect()` 加 `if _agent` 守卫 |
| 05-02 | run.sh 检测到 api.py 已运行后直接退出 | `set -e` + 输出全重定向到日志，终端无输出，误以为退出 | 改用 `2>&1 \| tee -a "$LOG_FILE"`，输出同时打到终端和日志 |
| 05-02 | run.sh api.py 启动失败后退出，不运行 fetch_news.py | `set -e` + `kill -0` 返回非零 | 在 else 分支加 `true` 屏蔽退出码 |
| 05-02 | run.sh 无法识别已运行的 api.py | `pgrep -f "python3 api.py"` 匹配不到完整路径 Python 进程 | 改用 `lsof -ti :5001` 检查端口占用 |
| 05-02 | 本地安装缺少 psycopg2 | requirements.txt 新增依赖未本地安装 | `pip3 install psycopg2-binary` |

---

---

## 2026-05-02（续）

### AI 热词榜重大升级

**数据库（api.py）**

- 新增 `topic_history` 表：`(keyword, heat, summary, date, count, UNIQUE(keyword, date))`，支持每日 UPSERT（count 累加）
- 新增 `topic_summaries` 表：`keyword TEXT PRIMARY KEY`，存储 brief / background / key_points / sources / updated_at
- 新增 `GET /topics?range=today/week/month`：按时段聚合，计算 trend（NEW / up / down / stable）
- 新增 `GET /topic/<keyword>`：返回详情 + 最近 30 天历史数组
- 新增 `POST /update_topics`：同时 upsert topic_history 和 topic_summaries
- `/health` 新增 `topic_count` 字段

**抓取与生成（fetch_news.py）**

- 新增 `generate_topic_summaries_with_claude(keywords, top_posts)`：一次调用 Claude 为所有热词生成 brief / background / key_points / sources
- 新增 `push_topics_to_cloud(keywords, summaries)`：POST 到 Railway `/update_topics`
- `main()` 在 `fetch_trending()` 后依次调用两个新函数
- `generate_trending_html()` 全面重设计：
  - **Tab 切换**：📅 今日 / 📆 本周 / 🗓 本月
  - **今日 Tab**：静态 HTML，关键词 Chip 可点击
  - **本周 / 本月 Tab**：JS 动态 fetch 云端 `/topics?range=` 并渲染，显示 trend 标签（NEW / ↑ / ↓ / →）
  - **详情 Modal**：点击 Chip 弹出，显示 brief / background / key_points / sources；本周本月点击额外 fetch `/topic/<keyword>` 展示历史趋势迷你柱形图（Canvas）
  - 支持 ESC 关闭、点击遮罩关闭
- 常量 `CLOUD_BASE = "https://web-production-6e883.up.railway.app"` 统一管理云端地址

**Reddit 帖子链接修复**（同一批次）

- 抓取时保留真实 permalink：`URL:https://reddit.com/r/…`
- Claude prompt 要求返回 `post_id`（数字字符串），本地建立 `url_map` 在解析后回填
- 彻底解决「查看原帖」404 问题

**Bug 修复**

| Bug | 修复方式 |
|-----|---------|
| Python 3.9 f-string 表达式不允许反斜杠 | 将含 `\\"` 的 fallback 字符串提取为 `kw_cloud_html` / `post_grid_html` 变量 |
| Agent think() JSON 解析失败（裸中文数组） | 删除 `priority_categories` 字段，保留 action / reason / targets |
| run.sh set -e + kill -0 导致主流程中断 | else 分支加 `true` |
| pgrep 匹配不到 api.py 进程 | 改用 `lsof -ti :5001` |
| 输出仅写日志不打终端 | 改用 `2>&1 \| tee -a "$LOG_FILE"` |

---

## 待办 / 已知问题

- [ ] Railway 部署时需在 web 服务 Variables 里手动设置 `DATABASE_URL`
- [ ] Agent `special_report` 和 `topic_summary` 生成的页面暂未加入导航栏

---

*本文档随每次开发更新，格式：日期 → 功能/修复描述。*
