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

## 待办 / 已知问题

- [ ] Railway 部署时需在 web 服务 Variables 里手动设置 `DATABASE_URL`
- [ ] Agent `special_report` 和 `topic_summary` 生成的页面暂未加入导航栏

---

*本文档随每次开发更新，格式：日期 → 功能/修复描述。*
