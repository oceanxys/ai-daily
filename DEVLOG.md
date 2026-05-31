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

## 2026-05-29

### 写路由 Bearer token 鉴权部署上线

5 个 `/update_*` 路由代码层鉴权早在 `f166ea2` 完成，本次补上配置端部署 + 端到端验证。

**配置**

- 生成 43 字符随机 token：`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
- 本机 `~/.zshrc` 加 `export API_WRITE_TOKEN='...'`
- Railway zooming-education → web service Variables 配置同一值，触发 redeploy
- 双向一致性验证：`diff <(echo -n "$API_WRITE_TOKEN") <(pbpaste)` 无输出，确认两边字节级相同

**端到端鉴权三连测试（目标 `POST /update_embeddings`，body `[]` 不污染数据）**

| 场景 | 期望 | 实际 |
|---|---|---|
| 无 Authorization 头 | 401 | ✅ 401 + "无效或缺失的 Bearer token" |
| 错误 Bearer token | 401 | ✅ 401 |
| 正确 Bearer token | 200 | ✅ 200, upserted=0 |
| 读路由 `/search` 无 token | 200 | ✅ 200，正常返回向量检索结果 |

**launchd 兼容性验证**

`/bin/bash -c 'source ~/.zshrc 2>/dev/null; python3 -c "import os; print(len(os.environ.get(\"API_WRITE_TOKEN\",\"\")))"'` 模拟 launchd 启动环境（不走交互式 shell），确认 fetch_news.py 在 22:00 自动跑时能正确加载 token，长度 43。

### GitHub PAT 轮换

`.git/config` remote URL 和 `~/.zshrc` `GITHUB_TOKEN` 原先都明文嵌 classic PAT（`ghp_*`）。撤销旧 PAT，新建 fine-grained PAT 替换两处。

---

## 2026-05-31

### launchd 实际跑修复两个隐藏 bug

5/29 上次提交后以为收工，5/31 晚检查 launchd 实际运行情况，发现 `runs = 32` 但 `last exit code = 2`，run.log 自 5/29 17:52 后从未更新过——**自动任务 5/29 和 5/30 两晚都没真正跑**。深入排查后发现两个 bug：

**Bug 1：run.sh 在 launchd 环境下静默早死**

根因：`set -e` + `source "$HOME/.zshrc"` 的经典坑。`.zshrc` 含 zsh 专属语法（如 bun completion 用了 `(N)` glob qualifier），bash 解析 sourced 文件遇到 syntax error 返回非零，**`set -e` 直接让父进程 exit 2，`|| true` 都接不住**。

修复 `run.sh`：把 source 包在临时关闭 errexit 的块里。

```bash
set +e
source "$HOME/.zshrc" 2>/dev/null
set -e
```

**Bug 2：修复 Bug 1 后，fetch_news.py 推送全部 HTTP 401**

run.sh 修好之后 fetch_news.py 终于完整跑通，但推送阶段（文章 / 向量 / 热词）**全部 401**。

根因：本机 `~/.zshrc` 有**两条 `export API_WRITE_TOKEN=...`**，token 值不同，后一行覆盖前一行。最终 source 出来的 token 与 Railway 上配置的 token 不一致，导致鉴权失败。

修复：删除 `~/.zshrc` 中错误的那一条 export，保留与 Railway 一致的那一条。

**端到端验证**

模拟 launchd 极简环境（`env -i HOME=$HOME PATH=/usr/bin:/bin bash -c 'source ~/.zshrc; curl ...'`）确认：
- source 后 `API_WRITE_TOKEN` 正确加载
- `POST /update_embeddings` 返回 `HTTP 200, upserted=0`

---

## 待办 / 已知问题

- [ ] Railway 部署时需在 web 服务 Variables 里手动设置 `DATABASE_URL`
- [ ] Agent `special_report` 和 `topic_summary` 生成的页面暂未加入导航栏

---

*本文档随每次开发更新，格式：日期 → 功能/修复描述。*
