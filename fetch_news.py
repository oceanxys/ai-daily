#!/usr/bin/env python3
"""AI导航中心 - 多数据源抓取 + Claude 总结 + 多页静态生成"""

import re
import json
import time
import sqlite3
import difflib
import html as html_lib
import os
import httpx
import openai
import feedparser
import anthropic
from typing import Optional, Union
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
from pathlib import Path
from brain import Agent

# ── 输出路径 ──

OUTPUT_DIR          = Path.home() / "Projects" / "ai-daily" / "output"
ARCHIVE_DIR         = Path.home() / "Projects" / "ai-daily" / "archive"
OUTPUT_PATH         = OUTPUT_DIR / "today.html"
ARCHIVE_HTML_PATH   = OUTPUT_DIR / "archive.html"
INDEX_HTML_PATH     = OUTPUT_DIR / "index.html"
MODELS_HTML_PATH    = OUTPUT_DIR / "models.html"
TRENDING_HTML_PATH  = OUTPUT_DIR / "trending.html"
BENCHMARK_HTML_PATH = OUTPUT_DIR / "benchmark.html"
TOOLS_HTML_PATH     = OUTPUT_DIR / "tools.html"
JOBS_HTML_PATH      = OUTPUT_DIR / "jobs.html"
DATA_DIR            = Path.home() / "Projects" / "ai-daily" / "data"
HIGHLIGHTS_PATH     = DATA_DIR / "highlights.json"
PAPERS_PATH         = DATA_DIR / "papers_today.json"

# ── 全局运行日志 ──
_RUN_LOG: dict = {}

# ── 导航 ──
NAV_PAGES = [
    ("🏠", "首页",     "index.html"),
    ("📊", "模型追踪", "models.html"),
    ("🔥", "热词榜",   "trending.html"),
    ("📈", "竞技场",   "benchmark.html"),
    ("🧰", "工具库",   "tools.html"),
    ("💼", "求职动态", "jobs.html"),
    ("📰", "今日日报", "today.html"),
    ("📚", "历史归档", "archive.html"),
]

# ── 共享 CSS（所有页面通用）──
SHARED_CSS = """
:root{
  --bg0:#0d1117;--bg1:#161b22;--bg2:#1c2128;--bg3:#21262d;
  --border:#30363d;
  --t1:#e6edf3;--t2:#8b949e;--t3:#6e7681;
  --blue:#58a6ff;--green:#3fb950;--purple:#bc8cff;
  --orange:#f0883e;--red:#ff7b72;--yellow:#f0c040;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg0);color:var(--t1);line-height:1.65;min-height:100vh}
.hdr{background:var(--bg1);border-bottom:1px solid var(--border);
  position:sticky;top:0;z-index:50;backdrop-filter:blur(8px)}
.hdr-inner{max-width:1300px;margin:0 auto;padding:.65rem 1.5rem;
  display:flex;align-items:center;gap:.6rem}
.brand{display:flex;align-items:center;gap:.55rem;text-decoration:none;flex-shrink:0}
.brand-icon{width:30px;height:30px;border-radius:7px;
  background:linear-gradient(135deg,var(--blue),var(--purple));
  display:flex;align-items:center;justify-content:center;font-size:.95rem}
.brand-name{font-size:.95rem;font-weight:700;
  background:linear-gradient(135deg,var(--blue),var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.nav{display:flex;align-items:center;gap:.1rem;flex-wrap:wrap;margin-left:auto}
.nav-item{padding:.25rem .58rem;border-radius:6px;color:var(--t2);
  text-decoration:none;font-size:.76rem;transition:all .15s;white-space:nowrap}
.nav-item:hover{background:var(--bg3);color:var(--t1)}
.nav-active{background:rgba(88,166,255,.12);color:var(--blue)}
.main{max-width:1300px;margin:0 auto;padding:2rem}
.ftr{text-align:center;padding:2rem;color:var(--t3);
  font-size:.74rem;border-top:1px solid var(--border);margin-top:2rem}
.ftr a{color:var(--blue);text-decoration:none}
.section{margin-bottom:2.5rem}
.section-hdr{display:flex;align-items:center;gap:.6rem;
  padding-bottom:.7rem;margin-bottom:1.25rem;
  border-bottom:2px solid var(--cat-color,var(--blue))}
.section-icon{font-size:1.15rem;line-height:1}
.section-title{font-size:.98rem;font-weight:700;color:var(--cat-color,var(--blue))}
.section-count{margin-left:auto;padding:.1rem .5rem;
  background:rgba(255,255,255,.06);border:1px solid var(--border);
  border-radius:20px;font-size:.7rem;color:var(--t2)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1.2rem}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;
  padding:1.2rem;display:flex;flex-direction:column;gap:.75rem;transition:all .18s ease}
.card:hover{background:var(--bg3);border-color:var(--blue);
  transform:translateY(-2px);box-shadow:0 8px 24px rgba(88,166,255,.1)}
.badge{padding:.2rem .62rem;background:rgba(88,166,255,.12);
  color:var(--blue);border:1px solid rgba(88,166,255,.2);
  border-radius:20px;font-size:.7rem;font-weight:600}
.stats{display:flex;align-items:center;gap:1.2rem;flex-wrap:wrap;
  background:var(--bg1);border:1px solid var(--border);border-radius:10px;
  padding:.8rem 1.3rem;margin-bottom:2rem;font-size:.83rem;color:var(--t2)}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.55;transform:scale(.8)}}
.stats strong{color:var(--t1)}
.dot-sep{color:var(--border)}
.no-data{text-align:center;padding:4rem;color:var(--t3);font-size:1rem}
@media(max-width:768px){
  .nav-item{font-size:.68rem;padding:.2rem .4rem}
  .main{padding:1rem}
  .grid{grid-template-columns:1fr}
}
"""


def _page_shell(title: str, active_url: str, body: str, extra_css: str = "") -> str:
    now = datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    nav_html = ""
    for emoji, label, url in NAV_PAGES:
        cls = " nav-active" if url == active_url else ""
        nav_html += f'<a href="{url}" class="nav-item{cls}">{emoji} {label}</a>'
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · AI 导航</title>
<style>
{SHARED_CSS}
{extra_css}
</style>
</head>
<body>
<header class="hdr">
  <div class="hdr-inner">
    <a class="brand" href="index.html">
      <div class="brand-icon">🤖</div>
      <div class="brand-name">AI 导航</div>
    </a>
    <nav class="nav">{nav_html}</nav>
  </div>
</header>
<main class="main">
{body}
</main>
<footer class="ftr">
  AI 导航中心 · Powered by <a href="https://www.anthropic.com" target="_blank">Claude</a> · {date_str}
</footer>
</body>
</html>"""


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

AI_KEYWORDS = [
    "AI", "artificial intelligence", "machine learning", "deep learning",
    "GPT", "LLM", "large language model", "Claude", "OpenAI", "Anthropic",
    "neural network", "generative", "chatbot", "automation", "robot",
    "transformer", "diffusion", "multimodal",
]


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def is_recent(entry, hours: int = 36) -> bool:
    """文章发布时间在最近 hours 小时内（宽松窗口，覆盖时区差异和跨日边界）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                pub = datetime(*parsed[:6], tzinfo=timezone.utc)
                return pub >= cutoff
            except Exception:
                pass
    return True  # 无时间戳的条目默认保留


def has_ai_keyword(entry) -> bool:
    text = (getattr(entry, "title", "") + " " + getattr(entry, "summary", "")).lower()
    return any(kw.lower() in text for kw in AI_KEYWORDS)


def _fetch_rss(url: str, timeout: int = FETCH_TIMEOUT):
    """用 httpx 带超时抓取 RSS，返回 feedparser 解析结果。"""
    with httpx.Client(headers=_FETCH_HEADERS, follow_redirects=True, timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return feedparser.parse(resp.content)


def _check_quality_with_claude(articles: list[dict]) -> dict:
    """让 Claude 判断内容质量，返回 {sufficient, reason, issues}。"""
    titles = "\n".join(f"- {a['title']}" for a in articles[:30])
    prompt = f"""以下是今日抓取到的 AI 新闻标题列表（共 {len(articles)} 条）：

{titles}

请判断这批内容的质量，输出 JSON：
{{
  "sufficient": true,
  "reason": "内容充足，主题多样",
  "issues": []
}}

判断标准：
- sufficient=false：超过50%内容高度重复，或内容价值极低（纯广告/无关内容）
- sufficient=true：内容多样、有价值，哪怕数量不多也算充足
字符串不含双引号。"""
    result = _claude_json(prompt, max_tokens=300)
    return result if result else {"sufficient": True, "reason": "质量检测跳过", "issues": []}


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
    cloud_url = "https://web-production-6e883.up.railway.app/update_papers"
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(cloud_url, json=payload)
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

_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


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


def _claude_json(prompt: str, max_tokens: int = 3000) -> Optional[Union[dict, list]]:
    client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"  ⚠ Claude JSON 解析失败: {e}")
    return None


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
#  Claude 新闻总结
# ══════════════════════════════════════════════

SYSTEM_PROMPT = """你是专业的AI行业分析师，负责整理每日AI资讯。
对每篇文章用中文撰写简洁摘要，突出技术价值与行业意义，语气客观专业。"""

MAX_ARTICLES = 30
BATCH_SIZE   = 10


def _clean(text: str) -> str:
    return text.replace('"', "'").replace('\n', ' ').replace('\r', '').strip()


def _summarize_batch(client, batch: list[dict], offset: int, retries: int = 2) -> list[dict]:
    articles_text = ""
    for i, a in enumerate(batch, 1):
        articles_text += (
            f"\n文章{i}:\n"
            f"来源: {_clean(a['source'])}\n"
            f"标题: {_clean(a['title'])}\n"
            f"链接: {a['link']}\n"
            f"摘要: {_clean(a['summary'])}\n---\n"
        )
    prompt = f"""请对以下AI新闻文章进行总结，并对每篇文章分类。

分类只能从以下选项中选一个：
- 大模型动态（模型发布、更新、性能评测）
- AI产品与工具（新产品发布、功能更新）
- AI研究进展（论文、技术突破）
- AI商业动态（融资、收购、合作）
- AI政策与监管（法规、政府动态）
- 其他（不属于以上任何类别）

严格按以下 JSON 格式输出，不要添加任何其他文字：

{{
  "summaries": [
    {{
      "index": 1,
      "chinese_title": "中文标题（15字以内）",
      "chinese_summary": "中文摘要（100~150字）",
      "category": "大模型动态",
      "original_title": "原文标题",
      "link": "链接",
      "source": "来源"
    }}
  ]
}}

注意：所有字符串不含双引号，请用单引号替代。

文章列表：
{articles_text}"""

    for attempt in range(retries + 1):
        try:
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                response_text = stream.get_final_message().content[0].text
            m = re.search(r"\{.*\}", response_text, re.DOTALL)
            if m:
                data = json.loads(m.group())
                results = data.get("summaries", [])
                for r in results:
                    r["index"] = r.get("index", 0) + offset
                return results
        except json.JSONDecodeError as e:
            print(f"  ⚠ JSON 解析失败（尝试 {attempt+1}）: {e}")
            if attempt < retries:
                time.sleep(2)
        except Exception as e:
            print(f"  ⚠ API 请求失败（尝试 {attempt+1}）: {e}")
            if attempt < retries:
                time.sleep(5)

    return [
        {
            "index": offset + i + 1,
            "chinese_title":   a["title"][:30],
            "chinese_summary": a["summary"][:150],
            "category": "其他",
            "original_title":  a["title"],
            "link":   a["link"],
            "source": a["source"],
        }
        for i, a in enumerate(batch)
    ]


def summarize_with_claude(articles: list[dict]) -> list[dict]:
    if not articles:
        return []
    articles = articles[:MAX_ARTICLES]
    client   = anthropic.Anthropic()
    summaries = []
    batches  = [articles[i:i+BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]
    for idx, batch in enumerate(batches):
        print(f"  正在处理第 {idx+1}/{len(batches)} 批（{len(batch)} 篇）...")
        summaries.extend(_summarize_batch(client, batch, offset=idx * BATCH_SIZE))
    return summaries


# ══════════════════════════════════════════════
#  公共渲染辅助
# ══════════════════════════════════════════════

BRAND_COLORS = {
    "OpenAI":    "#10a37f",
    "Anthropic": "#cc785c",
    "Google":    "#4285f4",
    "Meta":      "#0866ff",
    "xAI":       "#9e9e9e",
    "Mistral":   "#f0883e",
}

SCORE_LABELS = [
    ("text",       "文字理解与写作"),
    ("code",       "代码能力"),
    ("reasoning",  "推理与数学"),
    ("multimodal", "多模态"),
    ("speed_cost", "速度与性价比"),
]

CATEGORIES = [
    ("大模型动态",   "🤖", "#58a6ff"),
    ("AI产品与工具", "🛠️", "#3fb950"),
    ("AI研究进展",   "🔬", "#bc8cff"),
    ("AI商业动态",   "💰", "#f0883e"),
    ("AI政策与监管", "🌍", "#ff7b72"),
    ("其他",         "📌", "#8b949e"),
]

# 分类元数据索引，方便按名称查找 emoji/color
_CAT_META = {cat: (emoji, color) for cat, emoji, color in CATEGORIES}


# ══════════════════════════════════════════════
#  记忆系统 (memory.db)
# ══════════════════════════════════════════════

MEMORY_DB       = Path.home() / "Projects" / "ai-daily" / "memory.db"
SIMIL_THRESHOLD = 0.80   # 标题相似度去重阈值


def _get_conn() -> sqlite3.Connection:
    """连接数据库并确保三张表存在。"""
    conn = sqlite3.connect(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            title    TEXT    NOT NULL,
            link     TEXT    UNIQUE NOT NULL,
            category TEXT    DEFAULT '其他',
            date     TEXT    NOT NULL,
            source   TEXT    DEFAULT '',
            pushed   INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_art_link ON articles(link);
        CREATE INDEX IF NOT EXISTS idx_art_date ON articles(date);

        CREATE TABLE IF NOT EXISTS topics (
            name             TEXT PRIMARY KEY,
            first_seen       TEXT NOT NULL,
            last_seen        TEXT NOT NULL,
            total_count      INTEGER DEFAULT 1,
            consecutive_days INTEGER DEFAULT 1,
            timeline         TEXT    DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS preferences (
            category   TEXT PRIMARY KEY,
            push_count INTEGER DEFAULT 0,
            weight     REAL    DEFAULT 1.0
        );
    """)
    conn.commit()
    return conn


def dedup_articles(articles: list[dict]) -> tuple[list[dict], int]:
    """
    对抓取的原始文章去重，返回 (保留列表, 过滤数量)。
    去重规则：
      1. 链接与 DB 中已有记录完全相同 → 过滤
      2. 标题与最近 30 天记录相似度 ≥ 80% → 过滤
      3. 本次批次内链接/高相似标题已出现 → 过滤
    """
    conn = _get_conn()
    db_titles = [
        r[0] for r in conn.execute(
            "SELECT title FROM articles WHERE date >= date('now', '-30 days')"
        ).fetchall()
    ]

    kept:        list[dict] = []
    seen_links:  set        = set()
    seen_titles: list[str]  = list(db_titles)
    filtered = 0

    for art in articles:
        link  = art.get("link", "")
        title = art.get("title", "")

        # 本次批次内链接去重
        if link and link in seen_links:
            filtered += 1
            continue

        # DB 精确链接匹配
        if link and conn.execute("SELECT 1 FROM articles WHERE link=?", (link,)).fetchone():
            filtered += 1
            continue

        # 标题模糊匹配（与 DB + 本次已保留）
        is_sim = False
        for t in seen_titles[-200:]:   # 只对比最近 200 条，控制速度
            if difflib.SequenceMatcher(None, title, t).ratio() >= SIMIL_THRESHOLD:
                is_sim = True
                break
        if is_sim:
            filtered += 1
            continue

        kept.append(art)
        if link:
            seen_links.add(link)
        seen_titles.append(title)

    conn.close()
    return kept, filtered


def save_articles_to_db(summaries: list[dict]) -> None:
    """将已总结的文章保存到 articles 表（忽略链接已存在的记录）。"""
    conn  = _get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    for s in summaries:
        link = s.get("link", "")
        if not link:
            continue
        title = s.get("chinese_title") or s.get("original_title", "")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO articles(title,link,category,date,source,pushed)"
                " VALUES(?,?,?,?,?,1)",
                (title, link, s.get("category", "其他"), today, s.get("source", "")),
            )
        except Exception:
            pass
    conn.commit()
    conn.close()


def push_articles_to_cloud(summaries: list[dict]) -> None:
    if not summaries:
        return
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    articles = [
        {
            "title":           s.get("original_title", ""),
            "chinese_title":   s.get("chinese_title", ""),
            "chinese_summary": s.get("chinese_summary", ""),
            "category":        s.get("category", "其他"),
            "source":          s.get("source", ""),
            "link":            s.get("link", ""),
        }
        for s in summaries
        if s.get("link")
    ]
    if not articles:
        return
    try:
        with httpx.Client(timeout=20) as c:
            r = c.post(
                f"{CLOUD_BASE}/update_articles",
                json={"articles": articles, "date": today},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                d = r.json()
                print(f"  ✅ 云端入库：新增 {d.get('inserted', 0)} 篇，跳过重复 {len(articles) - d.get('inserted', 0)} 篇")
            else:
                print(f"  ⚠ 文章推送失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠ 文章推送到云端失败: {e}")


def update_topics_db(focus: Optional[dict]) -> None:
    """将检测到的热点话题写入 topics 表，更新连续出现天数。"""
    if not focus or not focus.get("topic"):
        return
    conn      = _get_conn()
    today     = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    name      = focus["topic"]
    why       = focus.get("why", "")

    row = conn.execute("SELECT * FROM topics WHERE name=?", (name,)).fetchone()
    if not row:
        tl = json.dumps([{"date": today, "summary": why}], ensure_ascii=False)
        conn.execute(
            "INSERT INTO topics(name,first_seen,last_seen,total_count,consecutive_days,timeline)"
            " VALUES(?,?,?,1,1,?)",
            (name, today, today, tl),
        )
    else:
        last_seen = row["last_seen"]
        consec    = row["consecutive_days"]
        if last_seen == today:
            pass                    # 今天已更新，不重复
        elif last_seen == yesterday:
            consec += 1             # 连续延续
        else:
            consec = 1              # 连续中断，重置
        tl = json.loads(row["timeline"] or "[]")
        if not tl or tl[-1].get("date") != today:
            tl.append({"date": today, "summary": why})
        conn.execute(
            "UPDATE topics SET last_seen=?,total_count=total_count+1,"
            "consecutive_days=?,timeline=? WHERE name=?",
            (today, consec, json.dumps(tl, ensure_ascii=False), name),
        )
    conn.commit()
    conn.close()


def get_persistent_topics(min_days: int = 3) -> list[dict]:
    """返回连续出现 min_days 天及以上、且今天仍活跃的话题。"""
    conn  = _get_conn()
    today = datetime.now().strftime("%Y-%m-%d")
    rows  = conn.execute(
        "SELECT * FROM topics WHERE consecutive_days>=? AND last_seen=?"
        " ORDER BY consecutive_days DESC",
        (min_days, today),
    ).fetchall()
    result = []
    for r in rows:
        result.append({
            "name":             r["name"],
            "first_seen":       r["first_seen"],
            "consecutive_days": r["consecutive_days"],
            "timeline":         json.loads(r["timeline"] or "[]"),
        })
    conn.close()
    return result


def update_preferences_db(summaries: list[dict]) -> dict:
    """
    用 EMA 更新各分类权重，返回 {category: weight} 字典（已按权重降序排列）。
    权重公式：w = 0.8 * w_old + 0.2 * (cat_count / total)
    未出现的分类缓慢衰减：w = 0.8 * w_old
    """
    conn = _get_conn()
    # 确保所有分类都有初始行
    for cat, _, _ in CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO preferences(category,push_count,weight) VALUES(?,0,1.0)",
            (cat,),
        )

    if summaries:
        cat_counts: dict[str, int] = {}
        for s in summaries:
            c = s.get("category", "其他")
            cat_counts[c] = cat_counts.get(c, 0) + 1
        total = len(summaries)

        appeared = set(cat_counts.keys())
        for cat, cnt in cat_counts.items():
            conn.execute(
                "UPDATE preferences SET push_count=push_count+?,"
                " weight=0.8*weight+0.2*? WHERE category=?",
                (cnt, cnt / total, cat),
            )
        for cat, _, _ in CATEGORIES:
            if cat not in appeared:
                conn.execute(
                    "UPDATE preferences SET weight=0.8*weight WHERE category=?", (cat,)
                )

    conn.commit()
    rows = conn.execute(
        "SELECT category, weight, push_count FROM preferences ORDER BY weight DESC"
    ).fetchall()
    weights = {r["category"]: round(r["weight"], 4) for r in rows}
    conn.close()
    return weights


def get_memory_stats() -> dict:
    """供 archive.html 使用的记忆统计数据。"""
    conn = _get_conn()
    total_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    total_topics   = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]

    rows = conn.execute("""
        SELECT date, category, COUNT(*) AS cnt
        FROM   articles
        WHERE  date >= date('now', '-7 days')
        GROUP  BY date, category
        ORDER  BY date ASC
    """).fetchall()

    weekly: dict[str, dict[str, int]] = {}
    dates_set: set = set()
    for r in rows:
        d, cat, cnt = r["date"], r["category"], r["cnt"]
        dates_set.add(d)
        weekly.setdefault(cat, {})[d] = cnt

    persistent = conn.execute(
        "SELECT name, consecutive_days FROM topics"
        " WHERE consecutive_days >= 2 ORDER BY consecutive_days DESC LIMIT 8"
    ).fetchall()

    conn.close()
    return {
        "total_articles": total_articles,
        "total_topics":   total_topics,
        "weekly":         weekly,
        "dates":          sorted(dates_set),
        "persistent":     [{"name": r["name"], "days": r["consecutive_days"]} for r in persistent],
    }


def _render_tracking_block(persistent_topics: list[dict]) -> str:
    """生成「📌 持续追踪」HTML 区块。"""
    if not persistent_topics:
        return ""
    items_html = ""
    for pt in persistent_topics:
        tl_rows = "".join(
            f'<div class="tl-row">'
            f'<span class="tl-date">{ev.get("date","")}</span>'
            f'<span class="tl-summary">{ev.get("summary","")}</span>'
            f'</div>'
            for ev in pt.get("timeline", [])[-5:]   # 最近 5 条
        )
        items_html += f"""<div class="track-item">
  <div class="track-topic">{pt['name']}</div>
  <div class="track-timeline">{tl_rows if tl_rows else '<div class="tl-row"><span class="tl-summary" style="color:var(--t3)">暂无时间线记录</span></div>'}</div>
</div>"""
    first_seen = persistent_topics[0]["first_seen"] if persistent_topics else ""
    return f"""<div class="track-block">
  <div class="track-hdr">
    <span class="track-badge">📌 持续追踪</span>
    <span class="track-title">以下话题已连续追踪多天</span>
    <span class="track-days">最早自 {first_seen}</span>
  </div>
  <div class="track-items">{items_html}</div>
</div>"""


def _render_mem_stats_panel(stats: dict) -> str:
    """生成 archive.html 顶部的记忆统计面板 HTML。"""
    total_art  = stats.get("total_articles", 0)
    total_top  = stats.get("total_topics", 0)
    persistent = stats.get("persistent", [])
    weekly     = stats.get("weekly", {})
    dates      = stats.get("dates", [])

    # 最近 7 天趋势迷你图（每个分类一行 bar）
    trend_rows = ""
    if weekly and dates:
        max_cnt = max(
            (cnt for d_map in weekly.values() for cnt in d_map.values()),
            default=1,
        ) or 1
        for cat, d_map in list(weekly.items())[:5]:   # 最多展示5类
            _, color = _CAT_META.get(cat, ("📌", "#8b949e"))
            bars = ""
            for d in dates[-7:]:
                cnt = d_map.get(d, 0)
                h   = max(4, round(cnt / max_cnt * 40))
                bars += f'<div class="mini-bar" style="height:{h}px;background:{color}" title="{d}: {cnt}条"></div>'
            trend_rows += f'<div class="trend-row"><span class="trend-cat">{cat}</span><div class="trend-bars">{bars}</div></div>'

    # 持续话题标签
    ptags = "".join(
        f'<span class="ptag">{p["name"]} <em>{p["days"]}天</em></span>'
        for p in persistent
    )

    return f"""<div class="mem-panel">
  <div class="mem-stat"><span class="mem-label">总收录文章</span><span class="mem-value">{total_art}</span></div>
  <div class="mem-stat"><span class="mem-label">追踪话题数</span><span class="mem-value">{total_top}</span></div>
  <div class="mem-divider"></div>
  <div class="mem-trend-wrap">
    <div class="mem-trend-title">近7天各分类趋势</div>
    <div class="mem-trend">{trend_rows if trend_rows else '<span style="color:var(--t3);font-size:.78rem">暂无数据</span>'}</div>
  </div>
  {f'<div class="mem-divider"></div><div class="mem-ptags-wrap"><div class="mem-trend-title">持续追踪话题</div><div class="mem-ptags">{ptags}</div></div>' if ptags else ''}
</div>"""


def _mem_panel_css() -> str:
    return """
/* ── 持续追踪区块 ── */
.track-block{background:rgba(88,166,255,.06);border:1px solid rgba(88,166,255,.28);
  border-radius:12px;padding:1.2rem 1.5rem;margin-bottom:1.5rem}
.track-hdr{display:flex;align-items:center;gap:.7rem;margin-bottom:1rem;flex-wrap:wrap}
.track-badge{font-size:.7rem;font-weight:700;letter-spacing:.04em;
  padding:.2rem .65rem;background:rgba(88,166,255,.15);color:var(--blue);
  border:1px solid rgba(88,166,255,.3);border-radius:20px;flex-shrink:0}
.track-title{font-size:.92rem;font-weight:600;color:var(--t1)}
.track-days{font-size:.76rem;color:var(--t3);margin-left:auto}
.track-items{display:flex;flex-direction:column;gap:.9rem}
.track-item{border-left:2px solid rgba(88,166,255,.3);padding-left:1rem}
.track-topic{font-size:.88rem;font-weight:700;color:var(--blue);margin-bottom:.35rem}
.track-timeline{display:flex;flex-direction:column;gap:.22rem}
.tl-row{display:flex;gap:.8rem;align-items:flex-start;font-size:.78rem}
.tl-date{color:var(--t3);flex-shrink:0;width:5.5rem}
.tl-summary{color:var(--t2);line-height:1.5}

/* ── archive 记忆统计面板 ── */
.mem-panel{background:var(--bg1);border-bottom:1px solid var(--border);
  padding:.85rem 1.5rem;display:flex;align-items:center;gap:2rem;flex-wrap:wrap}
.mem-stat{display:flex;flex-direction:column;gap:.15rem;flex-shrink:0}
.mem-label{font-size:.65rem;color:var(--t3);text-transform:uppercase;letter-spacing:.07em}
.mem-value{font-size:1.15rem;font-weight:700;color:var(--t1)}
.mem-divider{width:1px;height:36px;background:var(--border);flex-shrink:0}
.mem-trend-wrap,.mem-ptags-wrap{display:flex;flex-direction:column;gap:.35rem}
.mem-trend-title{font-size:.67rem;color:var(--t3);text-transform:uppercase;letter-spacing:.06em}
.mem-trend{display:flex;flex-direction:column;gap:.22rem}
.trend-row{display:flex;align-items:flex-end;gap:.5rem}
.trend-cat{font-size:.72rem;color:var(--t2);width:7rem;text-align:right;flex-shrink:0}
.trend-bars{display:flex;align-items:flex-end;gap:3px}
.mini-bar{width:10px;border-radius:2px 2px 0 0;cursor:default;transition:opacity .15s}
.mini-bar:hover{opacity:.7}
.mem-ptags{display:flex;flex-wrap:wrap;gap:.4rem}
.ptag{font-size:.75rem;padding:.18rem .6rem;background:rgba(88,166,255,.1);
  color:var(--blue);border:1px solid rgba(88,166,255,.25);border-radius:20px}
.ptag em{font-style:normal;color:var(--t3);margin-left:.25rem}
"""


def _format_weight_log(weights: dict) -> str:
    """把权重字典格式化为日志字符串。"""
    if not weights:
        return "（无数据）"
    lines = []
    for cat, w in sorted(weights.items(), key=lambda x: -x[1]):
        bar = "█" * int(w * 20)
        lines.append(f"    {cat:<12} {w:.4f}  {bar}")
    return "\n" + "\n".join(lines)


def _sort_categories_by_weight(weights: dict) -> list[tuple]:
    """根据偏好权重对 CATEGORIES 重排序（其他类固定放末尾）。"""
    if not weights:
        return list(CATEGORIES)
    def sort_key(item):
        cat = item[0]
        if cat == "其他":
            return -1.0
        return weights.get(cat, 0.0)
    return sorted(CATEGORIES, key=sort_key, reverse=True)


def _stars(n: int, total: int = 5) -> str:
    n = max(0, min(total, int(n or 0)))
    return "⭐" * n + "☆" * (total - n)


def _news_card(item: dict) -> str:
    src      = item.get("source", "")
    ctitle   = item.get("chinese_title", item.get("original_title", ""))
    csummary = item.get("chinese_summary", "")
    otitle   = item.get("original_title", "")
    link     = item.get("link", "#")
    short    = (otitle[:55] + "…") if len(otitle) > 55 else otitle
    return f"""<div class="card">
  <div class="card-top"><span class="badge">{src}</span></div>
  <h2 class="card-title">{ctitle}</h2>
  <p class="card-summary">{csummary}</p>
  <div class="card-foot">
    <span class="orig-title" title="{otitle}">{short}</span>
    <a class="btn-read" href="{link}" target="_blank" rel="noopener">阅读原文 →</a>
  </div>
</div>"""


def _render_model_accordion(model_versions: list[dict]) -> str:
    if not model_versions:
        return '<div class="no-data">暂无模型数据</div>'
    items = ""
    for idx, co_data in enumerate(model_versions):
        company = co_data.get("company", "")
        brand   = BRAND_COLORS.get(company, "#58a6ff")
        models  = co_data.get("models", [])
        mcards  = ""
        for mdl in models:
            feat_li  = "".join(f"<li>{f}</li>" for f in mdl.get("latest_features", []))
            scores   = mdl.get("scores", {})
            score_rows = "".join(
                f'<div class="score-row"><span class="score-label">{lbl}</span>'
                f'<span class="score-stars">{_stars(scores.get(k, 0))}</span></div>'
                for k, lbl in SCORE_LABELS
            )
            mile_li  = "".join(f"<li>{ms}</li>" for ms in mdl.get("milestones", []))
            mcards += f"""<div class="mcard">
  <div class="mcard-name" style="border-color:{brand}">{mdl.get('name','')}</div>
  <div class="mcard-section"><div class="mcard-section-title">📢 最新功能更新</div>
    <ul class="feature-list">{feat_li}</ul></div>
  <div class="mcard-section"><div class="mcard-section-title">⭐ 能力评分</div>
    <div class="scores">{score_rows}</div></div>
  <div class="mcard-section"><div class="mcard-section-title">📜 历史里程碑</div>
    <ul class="milestone-list">{mile_li}</ul></div>
</div>"""
        open_attr = "open" if idx == 0 else ""
        items += f"""<details class="acc-item" {open_attr}>
  <summary class="acc-summary" style="--brand:{brand}">
    <div class="acc-left">
      <span class="acc-dot"></span>
      <span class="acc-company">{company}</span>
      <span class="acc-model-count">{len(models)} 个模型</span>
    </div>
    <span class="acc-chevron">›</span>
  </summary>
  <div class="acc-body"><div class="model-cards">{mcards}</div></div>
</details>"""
    return f'<div class="accordion">{items}</div>'


MODEL_CSS = """
.accordion{display:flex;flex-direction:column;gap:.6rem}
.acc-item{border:1px solid var(--border);border-radius:12px;overflow:hidden;transition:border-color .2s}
.acc-item[open]{border-color:var(--brand)}
.acc-summary{list-style:none;display:flex;align-items:center;justify-content:space-between;
  padding:.9rem 1.3rem;cursor:pointer;background:var(--bg2);
  border-left:3px solid var(--brand);user-select:none;gap:1rem}
.acc-summary::-webkit-details-marker{display:none}
.acc-summary:hover{background:var(--bg3)}
.acc-left{display:flex;align-items:center;gap:.7rem}
.acc-dot{width:10px;height:10px;border-radius:50%;background:var(--brand);flex-shrink:0}
.acc-company{font-weight:700;font-size:1rem;color:var(--brand)}
.acc-model-count{font-size:.72rem;color:var(--t3);background:var(--bg3);
  border:1px solid var(--border);border-radius:20px;padding:.1rem .5rem}
.acc-chevron{font-size:1.2rem;color:var(--t3);transition:transform .25s;flex-shrink:0}
.acc-item[open] .acc-chevron{transform:rotate(90deg)}
.acc-body{padding:1.1rem;background:var(--bg1)}
.model-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:.9rem}
.mcard{background:var(--bg2);border:1px solid var(--border);border-radius:10px;
  padding:1rem;display:flex;flex-direction:column;gap:.85rem}
.mcard-name{font-size:.92rem;font-weight:700;color:var(--t1);padding-bottom:.5rem;border-bottom:2px solid}
.mcard-section{display:flex;flex-direction:column;gap:.3rem}
.mcard-section-title{font-size:.68rem;font-weight:700;color:var(--t3);letter-spacing:.06em;text-transform:uppercase}
.feature-list,.milestone-list{list-style:none;padding-left:.1rem;display:flex;flex-direction:column;gap:.22rem}
.feature-list li{font-size:.8rem;color:var(--t2);padding-left:.9rem;position:relative}
.feature-list li::before{content:'▸';position:absolute;left:0;color:var(--blue);font-size:.68rem;top:.1rem}
.milestone-list li{font-size:.76rem;color:var(--t3);padding-left:.9rem;position:relative}
.milestone-list li::before{content:'·';position:absolute;left:.1rem;color:var(--t3)}
.scores{display:flex;flex-direction:column;gap:.15rem}
.score-row{display:flex;align-items:center;justify-content:space-between;gap:.5rem}
.score-label{font-size:.76rem;color:var(--t2);flex:1}
.score-stars{font-size:.68rem;letter-spacing:-.02em;white-space:nowrap}
"""

NEWS_CSS = """
.card-top{display:flex;align-items:center}
.card-title{font-size:.95rem;font-weight:600;color:var(--t1);line-height:1.5}
.card-summary{font-size:.85rem;color:var(--t2);line-height:1.75;flex:1}
.card-foot{display:flex;align-items:center;justify-content:space-between;gap:.65rem;
  padding-top:.65rem;border-top:1px solid var(--border)}
.orig-title{font-size:.7rem;color:var(--t3);flex:1;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-style:italic}
.btn-read{display:inline-flex;align-items:center;white-space:nowrap;
  padding:.28rem .72rem;border:1px solid rgba(88,166,255,.3);border-radius:6px;
  color:var(--blue);text-decoration:none;font-size:.75rem;font-weight:500;transition:all .15s}
.btn-read:hover{background:rgba(88,166,255,.1);border-color:var(--blue)}

/* ── 警告横幅 ── */
.warn-banner{display:flex;align-items:flex-start;gap:.6rem;
  background:rgba(255,123,114,.07);border:1px solid rgba(255,123,114,.3);
  border-radius:8px;padding:.75rem 1.1rem;margin-bottom:1.1rem;
  color:var(--red);font-size:.83rem;line-height:1.55}
.warn-icon{flex-shrink:0;font-size:1rem;margin-top:.05rem}

/* ── 今日焦点区块 ── */
.focus-block{background:linear-gradient(135deg,rgba(255,123,114,.07),rgba(240,136,62,.06));
  border:1px solid rgba(255,123,114,.35);border-radius:12px;
  padding:1.3rem 1.5rem;margin-bottom:2rem}
.focus-hdr{display:flex;align-items:center;gap:.75rem;margin-bottom:.65rem}
.focus-badge{font-size:.72rem;font-weight:700;letter-spacing:.04em;
  padding:.2rem .65rem;background:rgba(255,123,114,.15);color:var(--red);
  border:1px solid rgba(255,123,114,.3);border-radius:20px}
.focus-topic{font-size:1.05rem;font-weight:700;color:var(--t1)}
.focus-why{font-size:.84rem;color:var(--t2);margin-bottom:.9rem;line-height:1.6}
.focus-arts{display:flex;flex-direction:column;gap:.35rem}
.focus-art{font-size:.82rem;color:var(--t2);padding-left:1rem;position:relative}
.focus-art::before{content:'→';position:absolute;left:0;color:var(--red)}
.focus-art a{color:var(--t2);text-decoration:none}
.focus-art a:hover{color:var(--blue);text-decoration:underline}

/* ── 采集质量报告 ── */
.quality-report{margin-top:3rem;background:var(--bg1);border:1px solid var(--border);
  border-radius:10px;padding:1.1rem 1.5rem}
.quality-title{font-size:.72rem;font-weight:700;color:var(--t3);
  letter-spacing:.07em;text-transform:uppercase;margin-bottom:.8rem}
.quality-grid{display:flex;gap:2.5rem;flex-wrap:wrap}
.quality-item{display:flex;flex-direction:column;gap:.15rem}
.quality-label{font-size:.72rem;color:var(--t3)}
.quality-value{font-size:.92rem;font-weight:600;color:var(--t1)}
.quality-ok{color:var(--green)}
.quality-warn{color:var(--orange)}
.src-list{margin-top:.9rem;border-top:1px solid var(--border);padding-top:.8rem;
  display:flex;flex-direction:column;gap:.25rem}
.src-item{display:flex;align-items:center;gap:.5rem;font-size:.76rem;color:var(--t3)}
.src-ok{color:var(--green)}
.src-fail{color:var(--red)}

/* ── 精选论文区块 ── */
.paper-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;
  padding:1.2rem;display:flex;flex-direction:column;gap:.65rem;transition:all .18s}
.paper-card:hover{background:var(--bg3);border-color:var(--purple);
  transform:translateY(-2px);box-shadow:0 8px 24px rgba(188,140,255,.1)}
.paper-title{font-size:.95rem;font-weight:600;color:var(--t1);line-height:1.5}
.paper-summary{font-size:.85rem;color:var(--t2);line-height:1.7}
.paper-reason{font-size:.8rem;color:var(--purple);padding:.35rem .7rem;
  background:rgba(188,140,255,.08);border:1px solid rgba(188,140,255,.2);
  border-radius:6px;line-height:1.55}
.paper-reason::before{content:'💡 推荐理由：';font-weight:600}
.paper-link{display:inline-flex;align-items:center;gap:.3rem;color:var(--blue);
  text-decoration:none;font-size:.78rem;font-weight:500;
  padding:.25rem .65rem;border:1px solid rgba(88,166,255,.3);border-radius:6px;
  align-self:flex-start;transition:all .15s}
.paper-link:hover{background:rgba(88,166,255,.1);border-color:var(--blue)}
.paper-updated{font-size:.7rem;color:var(--t3);margin-left:auto}
"""


# ══════════════════════════════════════════════
#  精选论文（来自 api.py / 扣子 Bot）
# ══════════════════════════════════════════════

def read_highlights() -> list[dict]:
    """从 highlights.json 读取精选论文列表。"""
    if not HIGHLIGHTS_PATH.exists():
        return []
    try:
        data = json.loads(HIGHLIGHTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return data.get("highlights", [])
    except Exception:
        return []


def _render_highlights_block(highlights: list[dict]) -> str:
    """生成「📚 今日精选论文」HTML 区块。"""
    if not highlights:
        return ""
    cards = ""
    for p in highlights:
        title   = p.get("title", "（无标题）")
        summary = p.get("summary", "")
        reason  = p.get("reason", "")
        link    = p.get("arxiv_link", p.get("link", "#"))
        cards += f"""<div class="paper-card">
  <div class="paper-title">{title}</div>
  {f'<div class="paper-summary">{summary}</div>' if summary else ''}
  {f'<div class="paper-reason">{reason}</div>' if reason else ''}
  <a class="paper-link" href="{link}" target="_blank" rel="noopener">📄 arxiv →</a>
</div>"""

    # 读取更新时间
    updated_at = ""
    if HIGHLIGHTS_PATH.exists():
        try:
            meta = json.loads(HIGHLIGHTS_PATH.read_text(encoding="utf-8"))
            updated_at = meta.get("updated_at", "")
        except Exception:
            pass

    return f"""<section class="section">
  <div class="section-hdr" style="--cat-color:#bc8cff">
    <span class="section-icon">📚</span>
    <h2 class="section-title">今日精选论文</h2>
    <span class="section-count">{len(highlights)} 篇</span>
    {f'<span class="paper-updated">更新于 {updated_at}</span>' if updated_at else ''}
  </div>
  <div class="grid">{cards}</div>
</section>"""


# ══════════════════════════════════════════════
#  热度异常检测
# ══════════════════════════════════════════════

def detect_hot_topic(summaries: list[dict]) -> Optional[dict]:
    """检测是否有话题在3篇以上文章中出现。返回焦点数据或 None。"""
    if len(summaries) < 3:
        return None

    texts = "\n".join(
        f"[{i+1}] {s.get('chinese_title', '')}：{s.get('chinese_summary', '')[:100]}"
        for i, s in enumerate(summaries[:30])
    )
    prompt = f"""以下是今日 AI 新闻摘要（共 {len(summaries)} 条）：

{texts}

请分析是否有某个话题异常热门（同一关键词或核心议题在3篇及以上文章中出现）。

输出 JSON（字符串不含双引号）：
{{
  "detected": false,
  "topic": "",
  "why": "",
  "article_indices": []
}}

若检测到热点，detected=true，topic 填话题名称，why 填30字以内原因，
article_indices 填相关文章的序号列表（从1开始）。"""

    result = _claude_json(prompt, max_tokens=400)
    if not result or not result.get("detected"):
        return None

    indices = result.get("article_indices", [])
    related = [summaries[i - 1] for i in indices if isinstance(i, int) and 1 <= i <= len(summaries)]
    if not related:
        return None

    return {
        "topic":    result.get("topic", ""),
        "why":      result.get("why", ""),
        "articles": related,
    }


# ══════════════════════════════════════════════
#  今日日报 (today.html)
# ══════════════════════════════════════════════

_ACTION_META = {
    "normal":         ("正常流程",   "#8b949e"),
    "find_more":      ("补充抓取",   "#f0883e"),
    "special_report": ("专题生成",   "#bc8cff"),
    "topic_summary":  ("时间线整理", "#3fb950"),
}


def _agent_css() -> str:
    return """
/* ── Agent 决策区块 ── */
.agent-block{background:rgba(88,166,255,.04);border:1px solid rgba(88,166,255,.18);
  border-radius:12px;padding:.9rem 1.3rem;margin-bottom:1.3rem;
  display:flex;flex-direction:column;gap:.45rem}
.agent-hdr{display:flex;align-items:center;gap:.65rem;flex-wrap:wrap}
.agent-icon{font-size:1rem}
.agent-title{font-size:.72rem;font-weight:700;color:var(--t3);
  letter-spacing:.06em;text-transform:uppercase}
.agent-badge{font-size:.72rem;font-weight:700;padding:.15rem .55rem;
  border-radius:20px;border:1px solid transparent}
.agent-extra{font-size:.75rem;color:var(--orange)}
.agent-reason{font-size:.86rem;color:var(--t2);line-height:1.6}
.agent-targets{display:flex;flex-wrap:wrap;gap:.35rem}
.agent-tag{font-size:.73rem;padding:.15rem .55rem;background:rgba(88,166,255,.08);
  color:var(--blue);border:1px solid rgba(88,166,255,.2);border-radius:20px}
.agent-result{font-size:.77rem;color:var(--t3)}
"""


def _render_agent_block(decision: Optional[dict]) -> str:
    if not decision:
        return ""
    action  = decision.get("action", "normal")
    reason  = decision.get("reason", "")
    targets = decision.get("targets", [])
    status  = decision.get("_act_status", "")
    extra   = decision.get("_extra_articles", [])

    label, color = _ACTION_META.get(action, ("未知", "#8b949e"))

    targets_html = ""
    if targets:
        tags = "".join(f'<span class="agent-tag">{t}</span>' for t in targets)
        targets_html = f'<div class="agent-targets">{tags}</div>'

    extra_note = (
        f'<span class="agent-extra">补充了 {len(extra)} 篇文章</span>' if extra else ""
    )
    result_html = (
        f'<div class="agent-result">执行结果：{status}</div>'
        if status and status != "normal flow" else ""
    )

    return f"""<div class="agent-block">
  <div class="agent-hdr">
    <span class="agent-icon">🧠</span>
    <span class="agent-title">今日 Agent 决策</span>
    <span class="agent-badge" style="color:{color};border-color:{color};background:{color}18">{label}</span>
    {extra_note}
  </div>
  <div class="agent-reason">{reason}</div>
  {targets_html}
  {result_html}
</div>"""


def generate_today_html(
    summaries:         list[dict],
    warnings:          Optional[list] = None,
    focus:             Optional[dict] = None,
    quality_report:    Optional[dict] = None,
    persistent_topics: Optional[list] = None,
    filtered_count:    int = 0,
    category_order:    Optional[list] = None,
    highlights:        Optional[list] = None,
    agent_decision:    Optional[dict] = None,
) -> None:
    now      = datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    day_str  = weekdays[now.weekday()]
    time_str = now.strftime("%H:%M")
    count    = len(summaries)

    cat_list = category_order if category_order else CATEGORIES

    # ── 警告横幅 ──
    warn_html = ""
    for w in (warnings or []):
        warn_html += f'<div class="warn-banner"><span class="warn-icon">⚠️</span>{w}</div>\n'

    # ── 持续追踪区块 ──
    tracking_html = _render_tracking_block(persistent_topics or [])

    # ── 今日焦点区块 ──
    focus_html = ""
    if focus and focus.get("topic"):
        art_items = "".join(
            f'<div class="focus-art"><a href="{a.get("link","#")}" target="_blank" rel="noopener">'
            f'{a.get("chinese_title", a.get("original_title",""))}</a></div>'
            for a in focus.get("articles", [])[:6]
        )
        focus_html = f"""<div class="focus-block">
  <div class="focus-hdr">
    <span class="focus-badge">🔥 今日焦点</span>
    <span class="focus-topic">{focus['topic']}</span>
  </div>
  <div class="focus-why">{focus.get('why', '')}</div>
  <div class="focus-arts">{art_items}</div>
</div>"""

    # ── 新闻分类区块（按偏好权重排序）──
    grouped = {cat: [] for cat, _, _ in CATEGORIES}
    valid   = {cat for cat, _, _ in CATEGORIES}
    for item in summaries:
        cat = item.get("category", "其他")
        if cat not in valid:
            cat = "其他"
        grouped[cat].append(item)

    sections = ""
    if not summaries:
        sections = '<div class="no-data">😶 今日暂无 AI 资讯</div>'
    else:
        for cat, emoji, color in cat_list:
            items = grouped.get(cat, [])
            if not items:
                continue
            cards = "\n".join(_news_card(i) for i in items)
            sections += f"""<section class="section">
  <div class="section-hdr" style="--cat-color:{color}">
    <span class="section-icon">{emoji}</span>
    <h2 class="section-title">{cat}</h2>
    <span class="section-count">{len(items)}</span>
  </div>
  <div class="grid">{cards}</div>
</section>"""

    # ── 采集质量报告 ──
    report_html = ""
    if quality_report:
        src_count   = quality_report.get("source_count", 0)
        success_pct = quality_report.get("success_rate", 0)
        art_total   = quality_report.get("article_count", 0)
        fallback    = quality_report.get("fallback_triggered", False)
        src_rows    = ""
        for src_name, status in quality_report.get("sources", {}).items():
            ok   = not str(status).startswith("failed")
            icon = '<span class="src-ok">✅</span>' if ok else '<span class="src-fail">✗</span>'
            src_rows += (
                f'<div class="src-item">{icon} <span>{src_name}</span>'
                f'<span style="color:var(--t3);margin-left:auto">{status}</span></div>'
            )
        pct_cls  = "quality-ok" if success_pct >= 67 else "quality-warn"
        fall_cls = "quality-warn" if fallback else "quality-ok"
        filtered_note = (
            f'<div class="quality-item"><span class="quality-label">去重过滤</span>'
            f'<span class="quality-value quality-warn">{filtered_count} 条</span></div>'
            if filtered_count else ""
        )
        report_html = f"""<div class="quality-report">
  <div class="quality-title">📊 本次采集质量报告</div>
  <div class="quality-grid">
    <div class="quality-item">
      <span class="quality-label">数据来源</span>
      <span class="quality-value">{src_count} 个</span>
    </div>
    <div class="quality-item">
      <span class="quality-label">抓取成功率</span>
      <span class="quality-value {pct_cls}">{success_pct}%</span>
    </div>
    <div class="quality-item">
      <span class="quality-label">文章总数</span>
      <span class="quality-value">{art_total} 条</span>
    </div>
    <div class="quality-item">
      <span class="quality-label">备用源</span>
      <span class="quality-value {fall_cls}">{'已触发' if fallback else '未触发'}</span>
    </div>
    {filtered_note}
  </div>
  <div class="src-list">{src_rows}</div>
</div>"""

    stats_bar = f"""<div class="stats">
  <div class="pulse"></div>
  <span>今日收录 <strong>{count}</strong> 条 AI 资讯</span>
  <span class="dot-sep">·</span>
  <span>由 <strong style="color:var(--purple)">Claude Sonnet</strong> 智能总结</span>
  <span class="dot-sep">·</span>
  <span>{date_str} {day_str} {time_str}</span>
</div>"""

    highlights_html = _render_highlights_block(highlights or [])
    agent_html      = _render_agent_block(agent_decision)
    body = warn_html + agent_html + stats_bar + "\n" + tracking_html + focus_html + highlights_html + sections + report_html
    extra_css = NEWS_CSS + _mem_panel_css() + _agent_css()
    html = _page_shell(f"AI 日报 · {date_str}", "today.html", body, extra_css)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"  ✅ today.html 生成完毕: {OUTPUT_PATH}")


# ══════════════════════════════════════════════
#  模型追踪 (models.html)
# ══════════════════════════════════════════════

def generate_models_html(model_versions: list[dict]) -> None:
    total = sum(len(c.get("models", [])) for c in model_versions)
    body  = f"""<div class="section-hdr" style="--cat-color:#f0c040;margin-bottom:1.5rem">
  <span class="section-icon">📊</span>
  <h2 class="section-title">主流模型版本追踪</h2>
  <span class="section-count">{total} 个模型</span>
</div>
{_render_model_accordion(model_versions)}"""
    html = _page_shell("模型版本追踪", "models.html", body, MODEL_CSS)
    MODELS_HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✅ models.html 生成完毕")


# ══════════════════════════════════════════════
#  热词榜 (trending.html)
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


CLOUD_BASE = "https://web-production-6e883.up.railway.app"

TRENDING_CSS = """
.tab-bar{display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:1.5rem}
.tab-btn{background:none;border:none;padding:.6rem 1.4rem;font-size:.88rem;font-weight:600;
  color:var(--t3);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s}
.tab-btn.active{color:var(--blue);border-bottom-color:var(--blue)}
.tab-panel{display:none}.tab-panel.active{display:block}
.kw-cloud{display:flex;flex-wrap:wrap;gap:.6rem;margin-bottom:2rem}
.kw-chip{display:inline-flex;align-items:center;gap:.4rem;
  padding:.35rem .85rem;border-radius:20px;font-size:.82rem;
  background:var(--bg2);border:1px solid var(--border);cursor:pointer;transition:all .15s}
.kw-chip:hover{border-color:var(--blue);background:var(--bg3)}
.kw-emoji{font-size:1rem}
.kw-word{font-weight:600;color:var(--t1)}
.kw-heat{font-size:.7rem;padding:.1rem .4rem;border-radius:10px;margin-left:.15rem}
.heat-极热{background:rgba(255,123,114,.15);color:var(--red)}
.heat-热门{background:rgba(240,136,62,.15);color:var(--orange)}
.heat-上升中{background:rgba(63,185,80,.15);color:var(--green)}
.kw-summary{font-size:.72rem;color:var(--t3);margin-top:.1rem}
.trend-badge{font-size:.68rem;padding:.05rem .3rem;border-radius:8px;margin-left:.2rem;font-weight:600}
.trend-NEW{background:rgba(63,185,80,.2);color:var(--green)}
.trend-up{color:var(--green);font-weight:700}
.trend-down{color:var(--red);font-weight:700}
.trend-stable{color:var(--t3)}
.post-card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;
  padding:1rem;display:flex;flex-direction:column;gap:.5rem;transition:all .15s}
.post-card:hover{border-color:var(--blue);background:var(--bg3)}
.post-title{font-size:.9rem;font-weight:600;color:var(--t1)}
.post-meta{display:flex;align-items:center;gap:.75rem;font-size:.75rem;color:var(--t3)}
.post-score{color:var(--orange)}
.post-link{color:var(--blue);text-decoration:none;font-size:.78rem;margin-top:.2rem}
.post-link:hover{text-decoration:underline}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9000;
  align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal-box{background:var(--bg1);border:1px solid var(--border);border-radius:16px;
  max-width:600px;width:90vw;max-height:85vh;overflow-y:auto;padding:1.5rem;position:relative}
.modal-close{position:absolute;top:1rem;right:1rem;background:none;border:none;
  font-size:1.3rem;cursor:pointer;color:var(--t3);line-height:1}
.modal-kw{font-size:1.2rem;font-weight:700;color:var(--t1);margin-bottom:.4rem}
.modal-brief{font-size:.88rem;color:var(--t2);margin-bottom:1rem;
  padding:.5rem .8rem;background:var(--bg2);border-radius:8px}
.modal-section-title{font-size:.78rem;font-weight:700;color:var(--t3);
  text-transform:uppercase;letter-spacing:.05em;margin:.9rem 0 .35rem}
.modal-bg{font-size:.84rem;color:var(--t2);line-height:1.6}
.modal-points{padding-left:1.2rem;margin:0}
.modal-points li{font-size:.84rem;color:var(--t2);margin-bottom:.25rem}
.modal-sources a{font-size:.8rem;color:var(--blue);text-decoration:none;display:block;margin-bottom:.2rem}
.modal-sources a:hover{text-decoration:underline}
.history-chart{width:100%;height:80px;margin-top:.5rem}
.loading-spinner{text-align:center;padding:2rem;color:var(--t3);font-size:.88rem}
"""


def generate_topic_summaries_with_claude(keywords: list, top_posts: list) -> list:
    if not keywords:
        return []
    kw_names = [k.get("keyword", "") for k in keywords]
    posts_text = "\n".join(
        f"- {p.get('title', '')} ({p.get('source', '')})"
        for p in top_posts[:10]
    )
    prompt = f"""以下是今日 AI 热词列表，以及相关热门讨论帖子。请为每个关键词生成简报。

热词列表：{', '.join(kw_names)}

相关帖子：
{posts_text}

严格按以下 JSON 格式输出，不加任何其他文字：

{{
  "summaries": [
    {{
      "keyword": "关键词（与输入完全一致）",
      "brief": "一句话描述这个话题（30字内）",
      "background": "背景介绍（100字内）",
      "key_points": ["要点1", "要点2", "要点3"],
      "sources": ["来源描述1", "来源描述2"]
    }}
  ]
}}

为所有 {len(kw_names)} 个关键词各生成一条，字符串内容不含双引号。"""

    result = _claude_json(prompt, max_tokens=4000)
    return result.get("summaries", []) if result else []


def push_topics_to_cloud(keywords: list, summaries: list) -> None:
    today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
    topics_payload = [
        {
            "keyword": k.get("keyword", ""),
            "heat":    k.get("heat", "热门"),
            "summary": k.get("summary", ""),
            "date":    today,
            "count":   1,
        }
        for k in keywords
    ]
    payload = {"topics": topics_payload, "summaries": summaries}
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(
                f"{CLOUD_BASE}/update_topics",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                d = r.json()
                print(f"  ✅ 已推送 {d.get('topics_count', 0)} 个热词到云端")
            else:
                print(f"  ⚠ 热词推送失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠ 热词推送到云端失败: {e}")


def generate_highlights(papers: list) -> list:
    if not papers:
        return []
    candidates = []
    for p in papers[:50]:
        candidates.append(f"- 标题: {p.get('title', '')}\n  摘要: {p.get('abstract', '')[:200]}\n  URL: {p.get('arxiv_url', '')}")
    candidates_text = "\n\n".join(candidates)
    prompt = f"""你是一位 AI 研究领域的专家编辑。以下是今日 arXiv 论文列表，请从中筛选出 3 篇最有价值、最值得关注的论文。

选择标准：
1. 方法创新性强，解决了真实问题
2. 实用价值高，有实际落地意义
3. 影响面广，对领域有推动作用

论文列表：
{candidates_text}

请返回 JSON 格式（数组，3个元素）：
[
  {{
    "title": "论文英文原标题",
    "summary": "中文摘要，100字以内，说明核心贡献",
    "reason": "推荐理由，50字以内",
    "arxiv_url": "论文链接"
  }}
]

只返回 JSON 数组，不要其他内容。"""
    client = anthropic.Anthropic()
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"  ⚠ 精选论文生成失败: {e}")
    return []


def push_highlights_to_cloud(highlights: list) -> None:
    if not highlights:
        return
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(
                f"{CLOUD_BASE}/update_highlights",
                json=highlights,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                print(f"  ✅ 已推送 {len(highlights)} 篇精选论文到云端")
            else:
                print(f"  ⚠ 精选论文推送失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠ 精选论文推送到云端失败: {e}")


def generate_embeddings(articles: list, papers: list) -> list:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  ⚠ OPENAI_API_KEY 未配置，跳过向量化")
        return []

    items = []
    for a in articles:
        aid = a.get("id") or a.get("source_id")
        if not aid:
            continue
        text = f"{a.get('chinese_title', '')} {a.get('chinese_summary', '')}".strip()
        if text:
            items.append({
                "source_type": "article",
                "source_id":   aid,
                "content":     text,
                "metadata":    {
                    "title":    a.get("chinese_title", ""),
                    "date":     a.get("date", ""),
                    "category": a.get("category", ""),
                    "link":     a.get("link", ""),
                },
            })
    for p in papers:
        pid = p.get("id") or p.get("source_id")
        if not pid:
            continue
        text = f"{p.get('title', '')} {p.get('abstract', '')}".strip()
        if text:
            items.append({
                "source_type": "paper",
                "source_id":   pid,
                "content":     text,
                "metadata":    {
                    "title":      p.get("title", ""),
                    "arxiv_url":  p.get("arxiv_url", ""),
                    "categories": p.get("categories", []),
                },
            })

    if not items:
        return []

    try:
        client = openai.OpenAI(api_key=api_key)
        texts  = [it["content"] for it in items]
        # OpenAI 最多 2048 条/次，按批处理
        BATCH  = 500
        vectors = []
        for i in range(0, len(texts), BATCH):
            resp = client.embeddings.create(
                model="text-embedding-3-small",
                input=texts[i:i+BATCH],
            )
            vectors.extend([d.embedding for d in resp.data])

        for it, vec in zip(items, vectors):
            it["embedding"] = vec

        print(f"  ✅ 生成 {len(items)} 条向量（{len([x for x in items if x['source_type']=='article'])} 篇文章 + {len([x for x in items if x['source_type']=='paper'])} 篇论文）")
        return items
    except Exception as e:
        print(f"  ⚠ 向量生成失败: {e}")
        return []


def push_embeddings_to_cloud(embeddings: list) -> None:
    if not embeddings:
        return
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(
                f"{CLOUD_BASE}/update_embeddings",
                json=embeddings,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                d = r.json()
                print(f"  ✅ 已推送 {d.get('upserted', len(embeddings))} 条向量到云端")
            else:
                print(f"  ⚠ 向量推送失败: HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  ⚠ 向量推送到云端失败: {e}")


def generate_trending_html(data: dict, topic_summaries: list = None) -> None:
    now = datetime.now()
    keywords  = data.get("keywords", [])
    top_posts = data.get("top_posts", [])

    summaries_map: dict = {}
    if topic_summaries:
        for s in topic_summaries:
            summaries_map[s.get("keyword", "")] = s

    kw_html = ""
    for kw in keywords:
        heat    = kw.get("heat", "热门")
        keyword = kw.get("keyword", "")
        safe_kw = keyword.replace("&", "&amp;").replace('"', "&quot;")
        kw_html += (
            f'<div class="kw-chip" data-kw="{safe_kw}" onclick="openModal(this.dataset.kw)">'
            f'<span class="kw-emoji">{kw.get("emoji","🔥")}</span>'
            f'<div>'
            f'<span class="kw-word">{keyword}</span>'
            f'<span class="kw-heat heat-{heat}">{heat}</span>'
            f'<div class="kw-summary">{kw.get("summary","")}</div>'
            f'</div></div>'
        )

    post_html = ""
    for p in top_posts:
        post_html += (
            f'<div class="post-card">'
            f'<div class="post-title">{p.get("title","")}</div>'
            f'<div class="post-meta">'
            f'<span>{p.get("source","")}</span>'
            f'<span class="post-score">▲ {p.get("score",0)}</span>'
            f'</div>'
            f'<a class="post-link" href="{p.get("url","#")}" target="_blank" rel="noopener">查看原帖 →</a>'
            f'</div>'
        )

    summaries_js = json.dumps(summaries_map, ensure_ascii=False)
    kw_cloud_html  = kw_html  if kw_html  else '<div class="no-data">暂无热词数据</div>'
    post_grid_html = post_html if post_html else '<div class="no-data">暂无帖子数据</div>'

    body = (
        '<div id="modal-overlay" class="modal-overlay" onclick="if(event.target===this)closeModal()">'
        '<div class="modal-box">'
        '<button class="modal-close" onclick="closeModal()">✕</button>'
        '<div id="modal-kw" class="modal-kw"></div>'
        '<div id="modal-brief" class="modal-brief"></div>'
        '<div class="modal-section-title">背景</div>'
        '<div id="modal-bg" class="modal-bg"></div>'
        '<div class="modal-section-title">关键要点</div>'
        '<ul id="modal-points" class="modal-points"></ul>'
        '<div class="modal-section-title">相关来源</div>'
        '<div id="modal-sources" class="modal-sources"></div>'
        '<div id="modal-hist-title" class="modal-section-title" style="display:none">历史趋势</div>'
        '<canvas id="modal-chart" class="history-chart" style="display:none"></canvas>'
        '</div></div>'
        f'<div class="stats"><div class="pulse"></div>'
        f'<span>今日 AI 热词 <strong>{len(keywords)}</strong> 个</span>'
        f'<span class="dot-sep">·</span>'
        f'<span>热门帖子 <strong>{len(top_posts)}</strong> 条</span>'
        f'<span class="dot-sep">·</span>'
        f'<span>更新于 {now.strftime("%H:%M")}</span></div>'
        '<div class="tab-bar">'
        '<button class="tab-btn active" onclick="switchTab(\'today\',this)">📅 今日</button>'
        '<button class="tab-btn" onclick="switchTab(\'week\',this)">📆 本周</button>'
        '<button class="tab-btn" onclick="switchTab(\'month\',this)">🗓 本月</button>'
        '</div>'
        '<div id="tab-today" class="tab-panel active">'
        f'<section class="section"><div class="section-hdr" style="--cat-color:#ff7b72">'
        f'<span class="section-icon">🔥</span><h2 class="section-title">今日热词云</h2>'
        f'<span class="section-count">{len(keywords)}</span></div>'
        f'<div class="kw-cloud">{kw_cloud_html}</div></section>'
        f'<section class="section"><div class="section-hdr" style="--cat-color:#f0883e">'
        f'<span class="section-icon">💬</span><h2 class="section-title">热门帖子</h2>'
        f'<span class="section-count">{len(top_posts)}</span></div>'
        f'<div class="grid">{post_grid_html}</div></section>'
        '</div>'
        '<div id="tab-week" class="tab-panel">'
        '<div id="week-content"><div class="loading-spinner">加载中...</div></div></div>'
        '<div id="tab-month" class="tab-panel">'
        '<div id="month-content"><div class="loading-spinner">加载中...</div></div></div>'
        f'<script>\nconst SUMMARIES={summaries_js};\n'
        f'const CLOUD_API="{CLOUD_BASE}";\n'
        'const LOADED={};\n'
        'function switchTab(name,btn){\n'
        '  document.querySelectorAll(".tab-btn").forEach(function(b){b.classList.remove("active");});\n'
        '  document.querySelectorAll(".tab-panel").forEach(function(p){p.classList.remove("active");});\n'
        '  btn.classList.add("active");\n'
        '  document.getElementById("tab-"+name).classList.add("active");\n'
        '  if((name==="week"||name==="month")&&!LOADED[name]){\n'
        '    LOADED[name]=true;\n'
        '    loadTopics(name);\n'
        '  }\n'
        '}\n'
        'function trendLabel(t){\n'
        '  if(t==="NEW")return\'<span class="trend-badge trend-NEW">NEW</span>\';\n'
        '  if(t==="up")return\'<span class="trend-up"> ↑</span>\';\n'
        '  if(t==="down")return\'<span class="trend-down"> ↓</span>\';\n'
        '  return\'<span class="trend-stable"> →</span>\';\n'
        '}\n'
        'async function loadTopics(range){\n'
        '  var el=document.getElementById(range+"-content");\n'
        '  try{\n'
        '    var r=await fetch(CLOUD_API+"/topics?range="+range);\n'
        '    var d=await r.json();\n'
        '    var topics=d.topics||[];\n'
        '    if(!topics.length){el.innerHTML=\'<div class="no-data">暂无数据</div>\';return;}\n'
        '    var html=\'<div class="kw-cloud">\';\n'
        '    for(var i=0;i<topics.length;i++){\n'
        '      var t=topics[i];\n'
        '      var heat=t.heat||"热门";\n'
        '      html+=\'<div class="kw-chip" data-kw="\'+t.keyword.replace(/"/g,"&quot;")+\'" onclick="openModalCloud(this.dataset.kw)">\'+'
        '\'<span class="kw-emoji">🔥</span><div>\'+'
        '\'<span class="kw-word">\'+t.keyword+\'</span>\'+'
        '\'<span class="kw-heat heat-\'+heat+\'">\'+heat+\'</span>\'+'
        'trendLabel(t.trend)+'
        '\'<div class="kw-summary">\'+( t.summary||"")+\'</div></div></div>\';\n'
        '    }\n'
        '    html+=\'</div>\';\n'
        '    el.innerHTML=html;\n'
        '  }catch(e){\n'
        '    el.innerHTML=\'<div class="no-data">加载失败，请检查网络连接</div>\';\n'
        '  }\n'
        '}\n'
        'function openModal(keyword){\n'
        '  var s=SUMMARIES[keyword]||{};\n'
        '  showModal(keyword,s,null);\n'
        '}\n'
        'async function openModalCloud(keyword){\n'
        '  showModal(keyword,{},null);\n'
        '  try{\n'
        '    var r=await fetch(CLOUD_API+"/topic/"+encodeURIComponent(keyword));\n'
        '    var d=await r.json();\n'
        '    showModal(keyword,d,d.history||[]);\n'
        '  }catch(e){}\n'
        '}\n'
        'function showModal(keyword,s,history){\n'
        '  document.getElementById("modal-kw").textContent=keyword;\n'
        '  document.getElementById("modal-brief").textContent=s.brief||"（暂无简介）";\n'
        '  document.getElementById("modal-bg").textContent=s.background||"（暂无背景）";\n'
        '  var pts=s.key_points||[];\n'
        '  var ul=document.getElementById("modal-points");\n'
        '  ul.innerHTML="";\n'
        '  if(pts.length){for(var i=0;i<pts.length;i++){var li=document.createElement("li");li.textContent=pts[i];ul.appendChild(li);}}\n'
        '  else{ul.innerHTML="<li>暂无要点</li>";}\n'
        '  var srcs=s.sources||[];\n'
        '  var srcEl=document.getElementById("modal-sources");\n'
        '  srcEl.innerHTML=srcs.length?srcs.map(function(src){return\'<a href="#" target="_blank">\'+src+\'</a>\';}).join(""):"（暂无来源）";\n'
        '  var histEl=document.getElementById("modal-hist-title");\n'
        '  var canvas=document.getElementById("modal-chart");\n'
        '  if(history&&history.length>0){histEl.style.display="";canvas.style.display="";drawChart(canvas,history);}\n'
        '  else{histEl.style.display="none";canvas.style.display="none";}\n'
        '  document.getElementById("modal-overlay").classList.add("open");\n'
        '  document.body.style.overflow="hidden";\n'
        '}\n'
        'function closeModal(){\n'
        '  document.getElementById("modal-overlay").classList.remove("open");\n'
        '  document.body.style.overflow="";\n'
        '}\n'
        'document.addEventListener("keydown",function(e){if(e.key==="Escape")closeModal();});\n'
        'function drawChart(canvas,history){\n'
        '  var ctx=canvas.getContext("2d");\n'
        '  var w=canvas.parentElement.offsetWidth||500;\n'
        '  var h=80;canvas.width=w;canvas.height=h;\n'
        '  ctx.clearRect(0,0,w,h);\n'
        '  var counts=history.map(function(item){return Number(item.count)||0;});\n'
        '  var maxC=Math.max.apply(null,counts.concat([1]));\n'
        '  var barW=Math.floor(w/(counts.length+1));\n'
        '  var pad=Math.max(2,Math.floor(barW*0.15));\n'
        '  for(var i=0;i<counts.length;i++){\n'
        '    var c=counts[i];\n'
        '    var x=i*barW+pad;\n'
        '    var barH=Math.max(2,Math.floor((c/maxC)*(h-20)));\n'
        '    var y=h-barH-15;\n'
        '    ctx.fillStyle="#388bfd";\n'
        '    ctx.beginPath();\n'
        '    if(ctx.roundRect){ctx.roundRect(x,y,barW-pad*2,barH,3);}else{ctx.rect(x,y,barW-pad*2,barH);}\n'
        '    ctx.fill();\n'
        '    if(history[i]&&history[i].date){ctx.fillStyle="#8b949e";ctx.font="9px sans-serif";ctx.fillText(history[i].date.slice(5),x,h-2);}\n'
        '  }\n'
        '}\n'
        '</script>'
    )

    html = _page_shell("AI 热词榜", "trending.html", body, TRENDING_CSS)
    TRENDING_HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✅ trending.html 生成完毕")


# ══════════════════════════════════════════════
#  模型竞技场 (benchmark.html)
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


BENCHMARK_CSS = """
.bm-section{margin-bottom:2.5rem;background:var(--bg2);border:1px solid var(--border);
  border-radius:12px;padding:1.4rem}
.bm-title{font-size:1rem;font-weight:700;color:var(--t1);margin-bottom:.3rem}
.bm-desc{font-size:.8rem;color:var(--t3);margin-bottom:1.1rem}
.bar-row{display:flex;align-items:center;gap:.75rem;margin-bottom:.55rem}
.bar-model{width:160px;font-size:.8rem;color:var(--t2);text-align:right;flex-shrink:0;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{flex:1;background:var(--bg3);border-radius:4px;height:22px;overflow:hidden;position:relative}
.bar-fill{height:100%;border-radius:4px;display:flex;align-items:center;
  padding-left:.5rem;font-size:.75rem;font-weight:600;color:#fff;
  background:linear-gradient(90deg,var(--blue),var(--purple));
  transition:width .4s ease;min-width:2rem}
.bar-company{width:70px;font-size:.7rem;color:var(--t3);flex-shrink:0}
"""


def generate_benchmark_html(data: dict) -> None:
    benchmarks = data.get("benchmarks", [])
    body_parts = [f"""<div class="stats">
  <div class="pulse"></div>
  <span>收录 <strong>{len(benchmarks)}</strong> 个基准测试</span>
  <span class="dot-sep">·</span>
  <span>数据来源：PapersWithCode · lmsys</span>
</div>"""]

    for bm in benchmarks:
        models = sorted(bm.get("models", []), key=lambda x: x.get("score", 0), reverse=True)
        if not models:
            continue
        max_score = max(m.get("score", 0) for m in models) or 100
        rows = ""
        for m in models:
            score = m.get("score", 0)
            pct   = round(score / max_score * 100, 1)
            rows += f"""<div class="bar-row">
  <div class="bar-model" title="{m.get('model','')}">{m.get('model','')}</div>
  <div class="bar-track">
    <div class="bar-fill" style="width:{pct}%">{score}{bm.get('unit','')}</div>
  </div>
  <div class="bar-company">{m.get('company','')}</div>
</div>"""
        body_parts.append(f"""<div class="bm-section">
  <div class="bm-title">{bm.get('name','')}</div>
  <div class="bm-desc">{bm.get('description','')}</div>
  {rows}
</div>""")

    if len(body_parts) == 1:
        body_parts.append('<div class="no-data">暂无 Benchmark 数据</div>')

    html = _page_shell("模型竞技场", "benchmark.html", "\n".join(body_parts), BENCHMARK_CSS)
    BENCHMARK_HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✅ benchmark.html 生成完毕")


# ══════════════════════════════════════════════
#  AI 工具库 (tools.html)
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


TOOL_CATS = [
    ("写作",  "#58a6ff"), ("编程",  "#3fb950"), ("图像",  "#bc8cff"),
    ("音频",  "#f0883e"), ("效率",  "#f0c040"), ("框架",  "#ff7b72"),
    ("数据",  "#9e9e9e"),
]

TOOLS_CSS = """
.tool-card{background:var(--bg2);border:1px solid var(--border);border-radius:10px;
  padding:1rem;display:flex;flex-direction:column;gap:.5rem;transition:all .15s}
.tool-card:hover{border-color:var(--blue);background:var(--bg3)}
.tool-name{font-size:.95rem;font-weight:700;color:var(--t1)}
.tool-desc{font-size:.82rem;color:var(--t2);flex:1}
.tool-foot{display:flex;align-items:center;justify-content:space-between;
  font-size:.75rem;color:var(--t3)}
.tool-stars{color:var(--yellow)}
.tool-link{color:var(--blue);text-decoration:none}
.tool-link:hover{text-decoration:underline}
.cat-badge{padding:.18rem .55rem;border-radius:20px;font-size:.68rem;font-weight:600}
"""


def generate_tools_html(tools: list[dict]) -> None:
    cat_colors = dict(TOOL_CATS)
    grouped: dict[str, list] = {}
    for t in tools:
        cat = t.get("category", "其他")
        grouped.setdefault(cat, []).append(t)

    body_parts = [f"""<div class="stats">
  <div class="pulse"></div>
  <span>今日收录 <strong>{len(tools)}</strong> 款 AI 工具</span>
  <span class="dot-sep">·</span>
  <span>来源：GitHub Trending</span>
</div>"""]

    for cat, items in grouped.items():
        color = cat_colors.get(cat, "#8b949e")
        cards = ""
        for t in items:
            cards += f"""<div class="tool-card">
  <div class="tool-name">
    <span class="cat-badge" style="background:rgba(88,166,255,.1);color:{color};border:1px solid {color}40">{cat}</span>
    {t.get('name','')}
  </div>
  <div class="tool-desc">{t.get('description','')}</div>
  <div class="tool-foot">
    <span class="tool-stars">⭐ {t.get('stars','')}</span>
    <a class="tool-link" href="{t.get('link','#')}" target="_blank" rel="noopener">→ 查看</a>
  </div>
</div>"""
        body_parts.append(f"""<section class="section">
  <div class="section-hdr" style="--cat-color:{color}">
    <span class="section-icon">🧰</span>
    <h2 class="section-title">{cat}</h2>
    <span class="section-count">{len(items)}</span>
  </div>
  <div class="grid">{cards}</div>
</section>""")

    if not tools:
        body_parts.append('<div class="no-data">暂无工具数据</div>')

    html = _page_shell("AI 工具库", "tools.html", "\n".join(body_parts), TOOLS_CSS)
    TOOLS_HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✅ tools.html 生成完毕")


# ══════════════════════════════════════════════
#  AI 求职动态 (jobs.html)
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


JOBS_CSS = """
.job-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;
  padding:1.3rem;display:flex;flex-direction:column;gap:.8rem;transition:all .15s}
.job-card:hover{border-color:var(--blue);background:var(--bg3)}
.job-company{font-size:1rem;font-weight:700;color:var(--blue)}
.job-focus{font-size:.85rem;color:var(--t1);font-weight:500}
.job-roles{display:flex;flex-wrap:wrap;gap:.4rem}
.role-tag{padding:.18rem .55rem;background:rgba(88,166,255,.08);
  border:1px solid rgba(88,166,255,.2);border-radius:20px;
  font-size:.72rem;color:var(--blue)}
.job-trend{font-size:.8rem;color:var(--t3);line-height:1.5}
.job-link{color:var(--blue);text-decoration:none;font-size:.8rem}
.job-link:hover{text-decoration:underline}
"""


def generate_jobs_html(companies: list[dict]) -> None:
    cards = ""
    for co in companies:
        roles = "".join(f'<span class="role-tag">{r}</span>' for r in co.get("roles", []))
        cards += f"""<div class="job-card">
  <div class="job-company">{co.get('company','')}</div>
  <div class="job-focus">{co.get('focus','')}</div>
  <div class="job-roles">{roles}</div>
  <div class="job-trend">📈 {co.get('trend','')}</div>
  <a class="job-link" href="{co.get('link','#')}" target="_blank" rel="noopener">→ 查看招聘页面</a>
</div>"""

    body = f"""<div class="stats">
  <div class="pulse"></div>
  <span>覆盖 <strong>{len(companies)}</strong> 家顶级 AI 公司</span>
  <span class="dot-sep">·</span>
  <span>数据每日更新</span>
</div>
<div class="grid">{''.join(cards) if cards else '<div class="no-data">暂无招聘数据</div>'}</div>"""

    html = _page_shell("AI 求职动态", "jobs.html", body, JOBS_CSS)
    JOBS_HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✅ jobs.html 生成完毕")


# ══════════════════════════════════════════════
#  首页 (index.html)
# ══════════════════════════════════════════════

INDEX_CSS = """
.hero{text-align:center;padding:3rem 1rem 2.5rem;border-bottom:1px solid var(--border);margin-bottom:2.5rem}
.hero-date{font-size:.85rem;color:var(--t3);margin-bottom:.5rem}
.hero-title{font-size:2.2rem;font-weight:800;
  background:linear-gradient(135deg,var(--blue),var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  margin-bottom:.75rem}
.hero-brief{font-size:.95rem;color:var(--t2);max-width:560px;margin:0 auto 1.2rem;line-height:1.7}
.hero-tags{display:flex;justify-content:center;gap:.5rem;flex-wrap:wrap}
.hero-tag{padding:.25rem .7rem;background:var(--bg2);border:1px solid var(--border);
  border-radius:20px;font-size:.75rem;color:var(--t2)}
.hub-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1.25rem}
.hub-card{background:var(--bg2);border:1px solid var(--border);border-radius:14px;
  padding:1.5rem;display:flex;flex-direction:column;gap:.7rem;
  text-decoration:none;color:inherit;transition:all .2s ease}
.hub-card:hover{background:var(--bg3);border-color:var(--hub-color,var(--blue));
  transform:translateY(-3px);box-shadow:0 10px 30px rgba(0,0,0,.3)}
.hub-icon{font-size:2rem;line-height:1}
.hub-title{font-size:1.05rem;font-weight:700;color:var(--hub-color,var(--t1))}
.hub-desc{font-size:.82rem;color:var(--t2);line-height:1.6;flex:1}
.hub-action{font-size:.78rem;color:var(--hub-color,var(--blue));font-weight:500;margin-top:.25rem}
"""

HUB_CARDS = [
    ("📊", "模型版本追踪", "models.html",    "#58a6ff",
     "实时抓取 OpenAI / Anthropic / Google / Meta 官网，追踪最新模型版本、功能更新与历史里程碑"),
    ("🔥", "AI 热词榜",   "trending.html",   "#ff7b72",
     "每日抓取 Reddit AI 社区热帖，提炼今日最热话题关键词与代表性讨论"),
    ("📈", "模型竞技场",  "benchmark.html",  "#3fb950",
     "从 PapersWithCode 抓取最新 Benchmark 数据，可视化对比各大模型编程/推理/写作得分"),
    ("🧰", "AI 工具库",   "tools.html",      "#bc8cff",
     "每日扫描 GitHub Trending，精选最新 AI 工具，按写作/编程/图像/音频/效率分类展示"),
    ("💼", "AI 求职动态", "jobs.html",       "#f0883e",
     "抓取 OpenAI / Anthropic / Google / Meta / xAI 招聘页，总结各公司在招方向与行业趋势"),
    ("📰", "今日 AI 日报","today.html",      "#f0c040",
     "从 TechCrunch / arXiv / O'Reilly 抓取今日 AI 资讯，由 Claude 智能分类总结"),
    ("📚", "历史归档",    "archive.html",    "#9e9e9e",
     "按日期浏览每天的 AI 日报存档，左侧日期列表点击即可查看"),
]


def generate_index_html(summaries: list[dict]) -> None:
    now      = datetime.now()
    date_str = now.strftime("%Y年%m月%d日")
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    day_str  = weekdays[now.weekday()]

    if summaries:
        brief = f"今日共收录 {len(summaries)} 条 AI 资讯，涵盖大模型动态、AI 产品更新、研究进展等领域。"
    else:
        brief = "今日资讯整理中，请稍候访问各子页面获取最新内容。"

    tags = ["大模型动态", "AI 工具", "研究进展", "行业资讯", "Benchmark"]
    tags_html = "".join(f'<span class="hero-tag">{t}</span>' for t in tags)

    cards_html = ""
    for emoji, title, url, color, desc in HUB_CARDS:
        cards_html += f"""<a class="hub-card" href="{url}" style="--hub-color:{color}">
  <div class="hub-icon">{emoji}</div>
  <div class="hub-title">{title}</div>
  <div class="hub-desc">{desc}</div>
  <div class="hub-action">进入 →</div>
</a>"""

    body = f"""<div class="hero">
  <div class="hero-date">{date_str} &nbsp;{day_str}</div>
  <h1 class="hero-title">AI 导航中心</h1>
  <div class="hero-brief">{brief}</div>
  <div class="hero-tags">{tags_html}</div>
</div>
<div class="hub-grid">{cards_html}</div>"""

    html = _page_shell("AI 导航中心", "index.html", body, INDEX_CSS)
    INDEX_HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✅ index.html 生成完毕")


# ══════════════════════════════════════════════
#  历史归档
# ══════════════════════════════════════════════

def save_archive(summaries: list[dict]) -> None:
    if not summaries:
        return
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    data  = {
        "date": today,
        "articles": [
            {
                "chinese_title":   s.get("chinese_title", ""),
                "category":        s.get("category", "其他"),
                "chinese_summary": s.get("chinese_summary", ""),
                "original_title":  s.get("original_title", ""),
                "source":          s.get("source", ""),
                "link":            s.get("link", ""),
            }
            for s in summaries
        ],
    }
    out = ARCHIVE_DIR / f"{today}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ 归档已保存: {out}")


ARCHIVE_CSS = """
body{height:100vh;overflow:hidden;display:flex;flex-direction:column}
.layout{display:flex;flex:1;overflow:hidden}
.sidebar{width:210px;min-width:170px;background:var(--bg1);
  border-right:1px solid var(--border);overflow-y:auto;flex-shrink:0}
.sidebar-hdr{padding:.7rem 1rem;font-size:.7rem;font-weight:700;
  color:var(--t3);letter-spacing:.08em;border-bottom:1px solid var(--border);
  position:sticky;top:0;background:var(--bg1)}
.date-item{padding:.55rem 1rem;cursor:pointer;border-bottom:1px solid rgba(48,54,61,.5);
  font-size:.83rem;color:var(--t2);display:flex;justify-content:space-between;
  align-items:center;border-left:2px solid transparent;transition:all .12s}
.date-item:hover{background:var(--bg2);color:var(--t1)}
.date-item.active{background:rgba(88,166,255,.08);color:var(--blue);border-left-color:var(--blue)}
.date-count{font-size:.68rem;color:var(--t3);background:var(--bg3);
  padding:.08rem .4rem;border-radius:10px;flex-shrink:0}
.content{flex:1;overflow-y:auto;padding:1.5rem 2rem}
.day-header{display:flex;align-items:baseline;gap:1rem;margin-bottom:1.5rem;
  padding-bottom:.9rem;border-bottom:1px solid var(--border)}
.day-title{font-size:1.3rem;font-weight:700}
.day-count{font-size:.82rem;color:var(--t3)}
.card-top{display:flex}.card-title{font-size:.92rem;font-weight:600;color:var(--t1);line-height:1.5}
.card-summary{font-size:.83rem;color:var(--t2);line-height:1.75;flex:1}
.card-foot{display:flex;align-items:center;justify-content:space-between;gap:.6rem;
  padding-top:.6rem;border-top:1px solid var(--border)}
.orig-title{font-size:.68rem;color:var(--t3);flex:1;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-style:italic}
.btn-read{display:inline-flex;align-items:center;white-space:nowrap;
  padding:.25rem .68rem;border:1px solid rgba(88,166,255,.3);border-radius:6px;
  color:var(--blue);text-decoration:none;font-size:.73rem;font-weight:500;transition:all .15s}
.btn-read:hover{background:rgba(88,166,255,.1);border-color:var(--blue)}
@media(max-width:680px){
  body{height:auto;overflow:auto}
  .layout{flex-direction:column;overflow:visible}
  .sidebar{width:100%;max-height:180px;border-right:none;border-bottom:1px solid var(--border)}
  .content{padding:1rem}
}
"""

ARCHIVE_JS = """
const ARCHIVE_DATA = __ARCHIVE_JSON__;
const CATEGORIES = [
  {key:"大模型动态",emoji:"🤖",color:"#58a6ff"},
  {key:"AI产品与工具",emoji:"🛠️",color:"#3fb950"},
  {key:"AI研究进展",emoji:"🔬",color:"#bc8cff"},
  {key:"AI商业动态",emoji:"💰",color:"#f0883e"},
  {key:"AI政策与监管",emoji:"🌍",color:"#ff7b72"},
  {key:"其他",emoji:"📌",color:"#8b949e"},
];
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function renderDay(date){
  const data=ARCHIVE_DATA[date];if(!data)return;
  const arts=data.articles||[];
  const grouped={};CATEGORIES.forEach(c=>{grouped[c.key]=[];});
  arts.forEach(a=>{const cat=a.category||'其他';(grouped[cat]||(grouped['其他'])).push(a);});
  let html=`<div class="day-header"><h1 class="day-title">${date}</h1><span class="day-count">${arts.length} 条资讯</span></div>`;
  if(!arts.length){html+='<div class="no-data">😶 该日暂无归档资讯</div>';}
  else{
    CATEGORIES.forEach(cat=>{
      const items=grouped[cat.key]||[];if(!items.length)return;
      html+=`<section class="section"><div class="section-hdr" style="--cat-color:${cat.color}">
        <span class="section-icon">${cat.emoji}</span><h2 class="section-title">${cat.key}</h2>
        <span class="section-count">${items.length}</span></div><div class="grid">`;
      items.forEach(item=>{
        const ot=esc(item.original_title);const short=ot.length>55?ot.slice(0,55)+'…':ot;
        html+=`<div class="card"><div class="card-top"><span class="badge">${esc(item.source)}</span></div>
          <h2 class="card-title">${esc(item.chinese_title)}</h2>
          <p class="card-summary">${esc(item.chinese_summary)}</p>
          <div class="card-foot"><span class="orig-title" title="${ot}">${short}</span>
          <a class="btn-read" href="${esc(item.link)}" target="_blank" rel="noopener">阅读原文 →</a></div></div>`;
      });
      html+='</div></section>';
    });
  }
  document.getElementById('content').innerHTML=html;
  document.querySelectorAll('.date-item').forEach(el=>el.classList.toggle('active',el.dataset.date===date));
}
const dates=Object.keys(ARCHIVE_DATA).sort().reverse();
const listEl=document.getElementById('date-list');
if(!dates.length){listEl.innerHTML='<div style="padding:1rem;color:var(--t3);font-size:.8rem">暂无归档</div>';}
else{
  dates.forEach(date=>{
    const count=(ARCHIVE_DATA[date].articles||[]).length;
    const el=document.createElement('div');el.className='date-item';el.dataset.date=date;
    el.innerHTML=`<span>${date}</span><span class="date-count">${count}</span>`;
    el.onclick=()=>renderDay(date);listEl.appendChild(el);
  });
  renderDay(dates[0]);
}
"""


def generate_archive_html() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_data: dict = {}
    for f in sorted(ARCHIVE_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            archive_data[d.get("date", f.stem)] = d
        except Exception as e:
            print(f"  ⚠ 读取归档失败 {f.name}: {e}")

    archive_json = json.dumps(archive_data, ensure_ascii=False)
    nav_html = ""
    for emoji, label, url in NAV_PAGES:
        cls = " nav-active" if url == "archive.html" else ""
        nav_html += f'<a href="{url}" class="nav-item{cls}">{emoji} {label}</a>'

    now      = datetime.now()
    date_str = now.strftime("%Y年%m月%d日")

    mem_stats    = get_memory_stats()
    mem_panel    = _render_mem_stats_panel(mem_stats)
    mem_css      = _mem_panel_css()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>历史归档 · AI 导航</title>
<style>
{SHARED_CSS}
{ARCHIVE_CSS}
{mem_css}
</style>
</head>
<body>
<header class="hdr">
  <div class="hdr-inner">
    <a class="brand" href="index.html">
      <div class="brand-icon">🤖</div>
      <div class="brand-name">AI 导航</div>
    </a>
    <nav class="nav">{nav_html}</nav>
  </div>
</header>
{mem_panel}
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-hdr">📅 历史日期</div>
    <div id="date-list"></div>
  </aside>
  <main class="content" id="content">
    <div class="no-data">请从左侧选择日期</div>
  </main>
</div>
<script>
{ARCHIVE_JS.replace('__ARCHIVE_JSON__', archive_json)}
</script>
</body>
</html>"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✅ archive.html 生成完毕")


# ══════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════

def main():
    global _RUN_LOG
    t0 = time.time()
    _RUN_LOG = {
        "start_time":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sources":            {},
        "article_count":      0,
        "fallback_triggered": False,
        "hot_topic":          None,
        "warnings":           [],
    }

    print("🤖 AI 导航中心启动")
    print("=" * 52)

    print("\n📊 [1/8] 模型版本追踪")
    model_versions = fetch_model_versions()
    print(f"  获取到 {len(model_versions)} 家公司数据")

    print("\n📡 [2/8] 抓取 RSS 新闻")
    articles = fetch_news()

    print("\n📄 [2.1] 保存 arXiv 论文")
    papers_count = save_arxiv_papers()

    print("\n⭐ [2.2] 生成精选论文")
    _papers_for_highlights = []
    try:
        _pdata = json.loads((DATA_DIR / "papers_today.json").read_text(encoding="utf-8"))
        _papers_for_highlights = _pdata.get("papers", _pdata) if isinstance(_pdata, dict) else _pdata
    except Exception:
        pass
    highlights = generate_highlights(_papers_for_highlights)
    if highlights:
        print(f"  ✅ 生成 {len(highlights)} 篇精选论文")
        push_highlights_to_cloud(highlights)
    else:
        print("  ⚠ 精选论文生成失败或无数据")

    print("\n🧠 [2.3] Agent 大脑决策")
    _agent = None
    agent_decision = {
        "action": "normal", "reason": "Agent 未运行",
        "targets": [], "_extra_articles": [], "_act_status": "",
    }
    try:
        _agent = Agent()
        agent_decision = _agent.run()
    except Exception as _e:
        print(f"  ⚠ Agent 启动失败（不影响主流程）: {_e}")
    extra_articles = agent_decision.get("_extra_articles", [])
    if extra_articles:
        articles.extend(extra_articles)
        print(f"  + Agent 补充 {len(extra_articles)} 篇文章，当前合计 {len(articles)} 篇")

    print("\n🧹 [2.5] 去重过滤")
    articles, filtered_count = dedup_articles(articles)
    print(f"  过滤重复 {filtered_count} 条，剩余 {len(articles)} 条待总结")

    print("\n🧠 [3/8] Claude 智能总结")
    summaries = summarize_with_claude(articles)

    print("\n🔍 [3.5] 热度异常检测")
    focus = detect_hot_topic(summaries)
    if focus:
        _RUN_LOG["hot_topic"] = focus["topic"]
        print(f"  🔥 检测到热点话题：{focus['topic']}")
    else:
        print("  无异常热点")

    print("\n🧠 [3.6] 记忆写入")
    save_articles_to_db(summaries)
    push_articles_to_cloud(summaries)

    print("\n🔢 [3.7] 向量化")
    embeddings = generate_embeddings(summaries, _papers_for_highlights)
    push_embeddings_to_cloud(embeddings)
    update_topics_db(focus)
    weights = update_preferences_db(summaries)
    persistent_topics = get_persistent_topics(min_days=3)
    cat_order = _sort_categories_by_weight(weights)
    print(f"  ✅ 写入 {len(summaries)} 篇文章到 memory.db")
    if persistent_topics:
        names = "、".join(p["name"] for p in persistent_topics)
        print(f"  📌 持续追踪话题（≥3天）：{names}")
    else:
        print("  无持续追踪话题")

    print("\n🔥 [4/8] AI 热词榜")
    trending = fetch_trending()
    print("  生成热词简报...")
    topic_summaries = generate_topic_summaries_with_claude(
        trending.get("keywords", []),
        trending.get("top_posts", []),
    )
    print(f"  ✅ 生成 {len(topic_summaries)} 条简报")
    print("  推送热词到云端...")
    push_topics_to_cloud(trending.get("keywords", []), topic_summaries)

    print("\n📈 [5/8] 模型竞技场 Benchmark")
    benchmarks = fetch_benchmarks()

    print("\n🧰 [6/8] AI 工具库")
    tools = fetch_tools()

    print("\n💼 [7/8] AI 求职动态")
    jobs = fetch_jobs()

    print("\n🎨 [8/8] 生成所有页面")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 构建质量报告数据
    sources     = _RUN_LOG["sources"]
    total_src   = len(sources)
    success_src = sum(1 for v in sources.values() if not str(v).startswith("failed"))
    quality_report = {
        "source_count":       total_src,
        "success_rate":       round(success_src / total_src * 100) if total_src else 0,
        "article_count":      _RUN_LOG["article_count"],
        "fallback_triggered": _RUN_LOG["fallback_triggered"],
        "sources":            sources,
    }

    highlights = read_highlights()
    if highlights:
        print(f"  📚 读取到 {len(highlights)} 篇精选论文")

    save_archive(summaries)
    generate_index_html(summaries)
    generate_today_html(
        summaries,
        _RUN_LOG["warnings"],
        focus,
        quality_report,
        persistent_topics,
        filtered_count,
        cat_order,
        highlights,
        agent_decision,
    )
    generate_models_html(model_versions)
    generate_trending_html(trending, topic_summaries)
    generate_benchmark_html(benchmarks)
    generate_tools_html(tools)
    generate_jobs_html(jobs)
    generate_archive_html()

    elapsed = round(time.time() - t0, 1)
    _RUN_LOG["end_time"]        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _RUN_LOG["elapsed_seconds"] = elapsed

    print("\n" + "=" * 52)
    print("📋 运行日志摘要")
    print(f"  开始时间 : {_RUN_LOG['start_time']}")
    print(f"  结束时间 : {_RUN_LOG['end_time']}")
    print(f"  总耗时   : {elapsed} 秒")
    print(f"  文章总数 : {_RUN_LOG['article_count']} 条（去重过滤 {filtered_count} 条）")
    print(f"  备用源   : {'已触发' if _RUN_LOG['fallback_triggered'] else '未触发'}")
    print(f"  热点话题 : {_RUN_LOG['hot_topic'] or '无'}")
    print("  数据源状态:")
    for src, status in _RUN_LOG["sources"].items():
        icon = "✅" if not str(status).startswith("failed") else "✗ "
        print(f"    {icon} {src}: {status}")
    print(f"  分类偏好权重:{_format_weight_log(weights)}")
    print("=" * 52)

    if _agent:
        _agent.reflect(agent_decision, final_count=len(summaries),
                       result=agent_decision.get("_act_status", ""))

    print(f"\n🎉 完成！主页：file://{INDEX_HTML_PATH}")


if __name__ == "__main__":
    main()
