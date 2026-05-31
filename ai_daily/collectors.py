"""数据采集：RSS、arXiv、网页抓取、模型版本、Reddit 热词、Benchmark、Tools、Jobs。"""

import re
import json
import html as html_lib
from datetime import datetime
from typing import Optional

import httpx
import feedparser

from ai_daily.common import (
    _RUN_LOG,
    _FETCH_HEADERS,
    DATA_DIR,
    PAPERS_PATH,
    CLOUD_BASE,
    _AUTH_HEADERS,
    strip_html,
    is_recent,
    has_ai_keyword,
)
from ai_daily.llm import _claude_json, _check_quality_with_claude


# ══════════════════════════════════════════════
#  RSS 新闻抓取
# ══════════════════════════════════════════════

FETCH_TIMEOUT = 10   # 每个数据源超时秒数
MIN_ARTICLES  = 5    # 低于此数量触发备用源

RSS_SOURCES = [
    {
        "name":    "O'Reilly Radar",
        "url":     "https://www.oreilly.com/radar/feed/",
        "backup":  "https://oreilly.com/radar/feed/",
        "ai_filter": False,
    },
    {
        "name":    "arXiv CS.AI",
        "url":     "https://rss.arxiv.org/rss/cs.AI",
        "backup":  "https://export.arxiv.org/rss/cs.AI",
        "ai_filter": False,
    },
    {
        "name":    "TechCrunch",
        "url":     "https://techcrunch.com/feed/",
        "backup":  "https://feeds.feedburner.com/TechCrunch/",
        "ai_filter": True,
    },
]

FALLBACK_RSS = [
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "ai_filter": False},
    {"name": "VentureBeat",  "url": "https://venturebeat.com/feed/",                                     "ai_filter": True},
    {"name": "Wired AI",     "url": "https://www.wired.com/feed/category/artificial-intelligence/rss",   "ai_filter": False},
]


def _fetch_rss(url: str, timeout: int = FETCH_TIMEOUT):
    """用 httpx 带超时抓取 RSS，返回 feedparser 解析结果。"""
    with httpx.Client(headers=_FETCH_HEADERS, follow_redirects=True, timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return feedparser.parse(resp.content)


def _parse_source_entries(feed, source: dict) -> list[dict]:
    """从 feedparser 结果提取符合条件的文章。"""
    out = []
    for entry in feed.entries:
        if not is_recent(entry):
            continue
        if source.get("ai_filter") and not has_ai_keyword(entry):
            continue
        summary = strip_html(
            getattr(entry, "summary", "") or getattr(entry, "description", "")
        )[:600]
        out.append({
            "source":  source["name"],
            "title":   getattr(entry, "title", "无标题"),
            "link":    getattr(entry, "link", ""),
            "summary": summary,
        })
    return out


def fetch_news() -> list[dict]:
    articles = []
    warnings: list[str] = []

    # ── 主源 + 备用源 ──
    for source in RSS_SOURCES:
        name = source["name"]
        feed = None
        print(f"  抓取中: {name} ...")
        try:
            feed = _fetch_rss(source["url"])
            _RUN_LOG.setdefault("sources", {})[name] = "success"
        except Exception as e:
            print(f"    ⚠ 主源失败({e})，切换备用源...")
            backup = source.get("backup")
            if backup:
                try:
                    feed = _fetch_rss(backup)
                    _RUN_LOG.setdefault("sources", {})[name] = "success (backup)"
                    _RUN_LOG["fallback_triggered"] = True
                except Exception as e2:
                    _RUN_LOG.setdefault("sources", {})[name] = f"failed: {e2}"
                    print(f"    ✗ 备用源也失败: {e2}")
            else:
                _RUN_LOG.setdefault("sources", {})[name] = f"failed: {e}"

        if feed:
            articles.extend(_parse_source_entries(feed, source))

    print(f"  主源共抓取到 {len(articles)} 篇文章")

    # ── 数量不足时自动触发备用 RSS ──
    if len(articles) < MIN_ARTICLES:
        print(f"  ⚠ 文章数不足（{len(articles)} < {MIN_ARTICLES}），抓取备用源...")
        _RUN_LOG["fallback_triggered"] = True
        warnings.append(f"主源文章数不足（{len(articles)} 条），已自动补充备用源")
        for fsrc in FALLBACK_RSS:
            fname = fsrc["name"]
            try:
                feed = _fetch_rss(fsrc["url"])
                _RUN_LOG.setdefault("sources", {})[fname] = "success (fallback)"
                new_arts = _parse_source_entries(feed, fsrc)
                articles.extend(new_arts)
                print(f"    ✅ {fname}: +{len(new_arts)} 篇")
            except Exception as e:
                _RUN_LOG.setdefault("sources", {})[fname] = f"failed: {e}"
                print(f"    ✗ {fname} 失败: {e}")
        print(f"  补充后共 {len(articles)} 篇文章")
    else:
        # ── 数量够时让 Claude 判断质量 ──
        print("  🔍 质量检测中...")
        quality = _check_quality_with_claude(articles)
        if not quality.get("sufficient", True):
            reason = quality.get("reason", "内容质量不足")
            print(f"  ⚠ Claude 判断质量不足：{reason}，抓取备用源...")
            _RUN_LOG["fallback_triggered"] = True
            warnings.append(f"内容质量不足：{reason}")
            for fsrc in FALLBACK_RSS:
                fname = fsrc["name"]
                try:
                    feed = _fetch_rss(fsrc["url"])
                    _RUN_LOG.setdefault("sources", {})[fname] = "success (fallback)"
                    new_arts = _parse_source_entries(feed, fsrc)
                    articles.extend(new_arts)
                    print(f"    ✅ {fname}: +{len(new_arts)} 篇")
                except Exception as e:
                    _RUN_LOG.setdefault("sources", {})[fname] = f"failed: {e}"
                    print(f"    ✗ {fname} 失败: {e}")
            print(f"  补充后共 {len(articles)} 篇文章")
        else:
            print(f"  ✅ 质量检测通过：{quality.get('reason', 'OK')}")

    if not articles:
        warnings.append("所有数据源均无法访问，今日暂无资讯")

    _RUN_LOG["article_count"] = len(articles)
    _RUN_LOG["warnings"] = warnings
    print(f"  最终文章总数: {len(articles)} 篇")
    return articles


def save_arxiv_papers() -> int:
    """
    单独抓取 arXiv CS.AI RSS，提取富元数据，保存到 papers_today.json。
    字段：title、authors、abstract（前500字）、arxiv_url、published、categories
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    feed = None
    for url in ("https://rss.arxiv.org/rss/cs.AI", "https://export.arxiv.org/rss/cs.AI"):
        try:
            feed = _fetch_rss(url)
            break
        except Exception as e:
            print(f"    ⚠ {url} 失败: {e}")

    if feed is None:
        print("    ✗ arXiv 全部源不可用，跳过保存")
        return 0

    papers = []
    for entry in feed.entries:
        if not is_recent(entry):
            continue

        # 标题（去除换行）
        title = getattr(entry, "title", "").replace("\n", " ").strip()

        # 作者列表
        if hasattr(entry, "authors") and entry.authors:
            authors = [a.get("name", "") for a in entry.authors if a.get("name")]
        elif hasattr(entry, "author") and entry.author:
            authors = [entry.author]
        else:
            authors = []

        # 摘要前 500 字
        abstract = strip_html(getattr(entry, "summary", ""))[:500].strip()

        # arxiv 链接
        arxiv_url = getattr(entry, "link", "")

        # 发布日期
        published = ""
        pp = getattr(entry, "published_parsed", None)
        if pp:
            try:
                published = datetime(*pp[:6]).strftime("%Y-%m-%d")
            except Exception:
                pass

        # 分类标签
        tags = getattr(entry, "tags", [])
        categories = [t.get("term", "") for t in tags if t.get("term")]

        papers.append({
            "title":      title,
            "authors":    authors,
            "abstract":   abstract,
            "arxiv_url":  arxiv_url,
            "published":  published,
            "categories": categories,
        })

    payload = {
        "date":   datetime.now().strftime("%Y-%m-%d"),
        "count":  len(papers),
        "papers": papers,
    }
    PAPERS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  ✅ 已保存 {len(papers)} 篇论文到 papers_today.json")

    # 推送到云端 API
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{CLOUD_BASE}/update_papers", json=payload, headers=_AUTH_HEADERS)
            if resp.status_code == 200:
                print(f"  ✅ 已推送到云端 API（{resp.json().get('count', len(papers))} 篇）")
            else:
                print(f"  ⚠ 云端 API 返回 {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"  ⚠ 云端推送失败（不影响本地）: {e}")

    return len(papers)


# ══════════════════════════════════════════════
#  模型版本追踪
# ══════════════════════════════════════════════

MODEL_SOURCES = [
    ("OpenAI",    "https://raw.githubusercontent.com/openai/openai-python/main/README.md"),
    ("Anthropic", "https://docs.anthropic.com/en/docs/about-claude/models/overview"),
    ("Google",    "https://ai.google.dev/gemini-api/docs/models"),
    ("Meta",      "https://ai.meta.com/llama/"),
    ("xAI",       "https://x.ai/news"),
]


def _fetch_page(url: str, max_chars: int = 4000) -> str:
    try:
        with httpx.Client(headers=_FETCH_HEADERS, follow_redirects=True, timeout=20) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return f"[HTTP {resp.status_code}: 页面不可访问]"
            text = resp.text
            text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>",  " ", text, flags=re.DOTALL | re.IGNORECASE)
            text = strip_html(text)
            text = html_lib.unescape(text)
            text = re.sub(r"\s{2,}", " ", text).strip()
            return text[:max_chars]
    except Exception as e:
        return f"[抓取失败: {e}]"


def fetch_model_versions() -> list[dict]:
    page_contents = ""
    for company, url in MODEL_SOURCES:
        print(f"    抓取 {company} 页面 ...")
        page_contents += f"\n\n=== {company} ===\n{_fetch_page(url)}"

    prompt = f"""以下是从各 AI 公司官网抓取的页面文本，请整理成模型卡片数据。
页面无法访问时可结合自身知识补充。

{page_contents}

要求：
- 每个公司列出 1~3 个最新代表性模型
- latest_features：近期新增功能，每条 20 字以内，3~5 条
- scores：各维度整数评分 1~5（text 文字写作、code 代码、reasoning 推理数学、multimodal 多模态、speed_cost 速度性价比）
- milestones：里程碑，格式 'YYYY-MM 简述'，3~5 条
- 所有字符串不含双引号，用单引号替代

严格按以下 JSON 格式输出，不加任何其他文字：

{{
  "companies": [
    {{
      "company": "公司名",
      "models": [
        {{
          "name": "模型名称",
          "latest_features": ["功能1", "功能2"],
          "scores": {{"text":5,"code":4,"reasoning":4,"multimodal":5,"speed_cost":3}},
          "milestones": ["2023-03 首版发布"]
        }}
      ]
    }}
  ]
}}"""

    result = _claude_json(prompt, max_tokens=4096)
    return (result or {}).get("companies", [])


# ══════════════════════════════════════════════
#  热词 / Reddit 抓取
# ══════════════════════════════════════════════

def fetch_trending() -> dict:
    sources = [
        ("Reddit r/artificial",    "https://www.reddit.com/r/artificial/hot.json?limit=25"),
        ("Reddit r/MachineLearning","https://www.reddit.com/r/MachineLearning/hot.json?limit=25"),
    ]
    posts = []
    for name, url in sources:
        try:
            with httpx.Client(headers={"User-Agent": "AI-Daily-Bot/1.0"}, timeout=15, follow_redirects=True) as c:
                r = c.get(url)
                if r.status_code == 200:
                    for child in r.json().get("data", {}).get("children", []):
                        p = child.get("data", {})
                        posts.append({
                            "title":        p.get("title", ""),
                            "score":        p.get("score", 0),
                            "num_comments": p.get("num_comments", 0),
                            "url":          f"https://reddit.com{p.get('permalink','')}",
                            "source":       name,
                        })
        except Exception as e:
            print(f"    ⚠ {name} 抓取失败: {e}")

    sorted_posts = sorted(posts, key=lambda x: x["score"], reverse=True)[:40]
    posts_text = "\n".join(
        f"- [ID:{i}] [{p['source']}] {p['title']} (热度:{p['score']}) URL:{p['url']}"
        for i, p in enumerate(sorted_posts)
    )
    # 建立 ID → URL 映射，供解析时回填真实链接
    url_map = {str(i): p["url"] for i, p in enumerate(sorted_posts)}

    if not posts_text:
        posts_text = "（未能抓取，请基于 AI 圈近期热点补充）"
        url_map = {}

    prompt = f"""以下是今日 AI 社区热门帖子（每条含 ID 和 URL）。请提取热门话题关键词，并整理代表性讨论。

{posts_text}

严格按以下 JSON 格式输出，不加任何其他文字：

{{
  "keywords": [
    {{"keyword": "关键词", "heat": "极热/热门/上升中", "summary": "话题简述（20字内）", "emoji": "emoji"}}
  ],
  "top_posts": [
    {{"title": "中文标题", "original_title": "原标题", "score": 1234, "source": "来源", "post_id": "0"}}
  ]
}}

keywords 8~12 个，top_posts 6~8 条，字符串不含双引号。
post_id 填对应帖子的 ID 数字（字符串形式），不要填 URL。"""

    result = _claude_json(prompt)
    if result and url_map:
        for p in result.get("top_posts", []):
            pid = str(p.get("post_id", ""))
            p["url"] = url_map.get(pid, "https://www.reddit.com/r/artificial/")

    return result or {}


# ══════════════════════════════════════════════
#  Benchmark 抓取
# ══════════════════════════════════════════════

def fetch_benchmarks() -> dict:
    sources = [
        ("MMLU",      "https://paperswithcode.com/sota/multi-task-language-understanding-on-mmlu"),
        ("HumanEval", "https://paperswithcode.com/sota/code-generation-on-humaneval"),
    ]
    page_contents = ""
    for name, url in sources:
        page_contents += f"\n\n=== {name} ===\n{_fetch_page(url, max_chars=3000)}"

    prompt = f"""以下是从 benchmark 排行榜抓取的内容。请整理主流 AI 模型在各基准测试上的得分。

{page_contents}

结合自身知识（截至你的知识截止日期）补充无法从页面获取的数据。

严格按以下 JSON 格式输出，不加任何其他文字：

{{
  "benchmarks": [
    {{
      "name": "MMLU",
      "description": "综合知识理解，满分100%",
      "unit": "%",
      "models": [
        {{"model": "Claude 3.5 Sonnet", "company": "Anthropic", "score": 88.7}},
        {{"model": "GPT-4o", "company": "OpenAI", "score": 87.2}}
      ]
    }}
  ]
}}

包含 MMLU / HumanEval / MATH / GPQA 共 4 个基准，每个基准 6~8 个主流模型，字符串不含双引号。"""

    result = _claude_json(prompt, max_tokens=3000)
    return result or {}


# ══════════════════════════════════════════════
#  Tools 抓取
# ══════════════════════════════════════════════

def fetch_tools() -> list[dict]:
    sources = [
        ("GitHub Trending Python", "https://github.com/trending/python?since=daily"),
        ("GitHub Trending",        "https://github.com/trending?since=daily"),
    ]
    page_contents = ""
    for name, url in sources:
        page_contents += f"\n\n=== {name} ===\n{_fetch_page(url, max_chars=3500)}"

    prompt = f"""以下是从 GitHub Trending 抓取的今日热门项目。请提取其中与 AI/ML 相关的工具。

{page_contents}

如抓取内容不足，可结合自身知识补充近期热门 AI 工具。
分类：写作 / 编程 / 图像 / 音频 / 效率 / 框架 / 数据

严格按以下 JSON 格式输出，不加任何其他文字：

{{
  "tools": [
    {{
      "name": "工具名称",
      "category": "分类",
      "description": "一句话介绍（30字以内）",
      "stars": "star 数或评分",
      "link": "链接"
    }}
  ]
}}

提取 10~15 个工具，字符串不含双引号。"""

    result = _claude_json(prompt)
    return (result or {}).get("tools", [])


# ══════════════════════════════════════════════
#  Jobs 抓取
# ══════════════════════════════════════════════

JOBS_SOURCES = [
    ("OpenAI",          "https://openai.com/careers/"),
    ("Anthropic",       "https://www.anthropic.com/careers"),
    ("Google DeepMind", "https://deepmind.google/careers/"),
    ("Meta AI",         "https://www.metacareers.com/jobs"),
    ("xAI",             "https://x.ai/careers"),
]


def fetch_jobs() -> list[dict]:
    page_contents = ""
    for company, url in JOBS_SOURCES:
        page_contents += f"\n\n=== {company} ({url}) ===\n{_fetch_page(url, max_chars=3000)}"

    prompt = f"""以下是各大 AI 公司招聘页抓取内容，请分析各公司当前招聘重点。

{page_contents}

对无法抓取的页面，结合自身知识补充该公司近期招聘趋势。

严格按以下 JSON 格式输出，不加任何其他文字：

{{
  "companies": [
    {{
      "company": "公司名",
      "focus": "招聘重点方向（20字以内）",
      "roles": ["岗位1", "岗位2", "岗位3"],
      "trend": "趋势分析（30字以内）",
      "link": "招聘页面链接"
    }}
  ]
}}

覆盖 OpenAI/Anthropic/Google/Meta/xAI，字符串不含双引号。"""

    result = _claude_json(prompt)
    return (result or {}).get("companies", [])
