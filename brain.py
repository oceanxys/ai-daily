#!/usr/bin/env python3
"""Agent 大脑模块 - 感知→判断→行动→反思"""

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import feedparser
import httpx

MEMORY_DB   = Path.home() / "Projects" / "ai-daily" / "memory.db"
DATA_DIR    = Path.home() / "Projects" / "ai-daily" / "data"
OUTPUT_DIR  = Path.home() / "Projects" / "ai-daily" / "output"
PAPERS_PATH = DATA_DIR / "papers_today.json"

AGENT_EXTRA_SOURCES = [
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "ai_filter": False},
    {"name": "VentureBeat",  "url": "https://venturebeat.com/feed/",                                     "ai_filter": True},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index",                    "ai_filter": True},
]

_AI_KEYWORDS = [
    "AI", "artificial intelligence", "machine learning", "deep learning",
    "GPT", "LLM", "large language model", "Claude", "OpenAI", "Anthropic",
    "neural network", "generative", "chatbot", "automation", "transformer",
    "diffusion", "multimodal",
]

_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept":     "text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _has_ai_keyword(text: str) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in _AI_KEYWORDS)


class Agent:
    def __init__(self):
        self._ensure_tables()

    # ── 内部工具 ──────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(MEMORY_DB)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self):
        conn = self._get_conn()
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
            CREATE TABLE IF NOT EXISTS topics (
                name             TEXT PRIMARY KEY,
                first_seen       TEXT NOT NULL,
                last_seen        TEXT NOT NULL,
                total_count      INTEGER DEFAULT 1,
                consecutive_days INTEGER DEFAULT 1,
                timeline         TEXT    DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS agent_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                reason      TEXT    DEFAULT '',
                targets     TEXT    DEFAULT '[]',
                final_count INTEGER DEFAULT 0,
                result      TEXT    DEFAULT '',
                created_at  TEXT    NOT NULL
            );
        """)
        conn.commit()
        conn.close()

    # ── 感知阶段 ──────────────────────────────────────

    def sense(self) -> dict:
        today     = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        week_ago  = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        conn = self._get_conn()

        today_count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE date=?", (today,)
        ).fetchone()[0]

        yesterday_count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE date=?", (yesterday,)
        ).fetchone()[0]

        trend_rows = conn.execute(
            "SELECT date, COUNT(*) as cnt FROM articles WHERE date >= ? GROUP BY date ORDER BY date",
            (week_ago,),
        ).fetchall()
        daily_trend = {r["date"]: r["cnt"] for r in trend_rows}

        hot_topics = conn.execute(
            "SELECT name, consecutive_days, last_seen FROM topics"
            " WHERE consecutive_days >= 2 ORDER BY consecutive_days DESC LIMIT 5"
        ).fetchall()
        hot_topics_list = [
            {"name": r["name"], "consecutive_days": r["consecutive_days"], "last_seen": r["last_seen"]}
            for r in hot_topics
        ]

        long_topics = conn.execute(
            "SELECT name, consecutive_days FROM topics"
            " WHERE consecutive_days >= 5 ORDER BY consecutive_days DESC LIMIT 3"
        ).fetchall()
        long_topics_list = [{"name": r["name"], "days": r["consecutive_days"]} for r in long_topics]

        conn.close()

        paper_count = 0
        if PAPERS_PATH.exists():
            try:
                data = json.loads(PAPERS_PATH.read_text(encoding="utf-8"))
                paper_count = data.get("count", len(data.get("papers", [])))
            except Exception:
                pass

        avg_daily = round(sum(daily_trend.values()) / len(daily_trend), 1) if daily_trend else 0

        return {
            "today":                today,
            "today_count":          today_count,
            "yesterday_count":      yesterday_count,
            "paper_count":          paper_count,
            "avg_daily_articles":   avg_daily,
            "daily_trend":          daily_trend,
            "hot_topics":           hot_topics_list,
            "long_running_topics":  long_topics_list,
        }

    # ── 判断阶段 ──────────────────────────────────────

    def think(self, state: dict) -> dict:
        state_text = json.dumps(state, ensure_ascii=False, indent=2)
        prompt = f"""你是一个 AI 新闻聚合系统的决策 Agent。以下是今日系统感知到的状态数据：

{state_text}

请按以下优先级规则做出唯一决策，输出严格合法的 JSON，所有字符串必须用双引号包裹：

优先级（从高到低）：
1. long_running_topics 不为空（有话题连续5天以上）→ topic_summary
2. 某个 hot_topics 的 consecutive_days >= 5 → special_report
3. yesterday_count < 10（昨日文章不足10条）→ find_more
4. 其他情况 → normal

严格按以下格式输出，不要添加其他文字：
{{
  "action": "normal",
  "reason": "决策理由30字以内",
  "targets": [],
  "priority_categories": []
}}

action 只能是 normal / find_more / special_report / topic_summary 之一。"""

        client = anthropic.Anthropic()
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text
            text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
            # 修复数组中裸字符串（如 [科技, 财经] → ["科技", "财经"]）
            text = re.sub(
                r'\[([^\[\]"]+?)\]',
                lambda mo: "[" + ", ".join(
                    f'"{w.strip()}"' if w.strip() and not w.strip().startswith('"') else w.strip()
                    for w in mo.group(1).split(",")
                ) + "]",
                text,
            )
            # 尝试直接解析
            try:
                return json.loads(text)
            except Exception:
                pass
            # 提取第一个 JSON 对象
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as e:
            print(f"  ⚠ Agent think 失败: {e}")

        return {
            "action":              "normal",
            "reason":              "决策模块异常，采用默认流程",
            "targets":             [],
            "priority_categories": [],
        }

    # ── 行动阶段 ──────────────────────────────────────

    def act(self, decision: dict) -> dict:
        action = decision.get("action", "normal")

        if action == "normal":
            return {"status": "normal flow", "extra_articles": []}

        if action == "find_more":
            return self._find_more()

        if action == "special_report":
            targets = decision.get("targets", [])
            topic   = targets[0] if targets else "AI 前沿进展"
            path    = self._generate_special_report(topic)
            return {"status": f"已生成专题报告: {Path(path).name}", "extra_articles": []}

        if action == "topic_summary":
            targets = decision.get("targets", [])
            if not targets:
                conn = self._get_conn()
                rows = conn.execute(
                    "SELECT name FROM topics WHERE consecutive_days >= 5 ORDER BY consecutive_days DESC LIMIT 1"
                ).fetchall()
                conn.close()
                targets = [r["name"] for r in rows]
            paths = [self._generate_topic_summary(t) for t in targets[:1] if t]
            return {"status": f"已生成话题时间线: {', '.join(Path(p).name for p in paths if p)}", "extra_articles": []}

        return {"status": "unknown action", "extra_articles": []}

    def _find_more(self) -> dict:
        extra = []
        for source in AGENT_EXTRA_SOURCES:
            if len(extra) >= 15:
                break
            try:
                with httpx.Client(headers=_FETCH_HEADERS, follow_redirects=True, timeout=10) as client:
                    resp = client.get(source["url"])
                    resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                added = 0
                for entry in feed.entries:
                    title   = getattr(entry, "title", "")
                    summary = _strip_html(getattr(entry, "summary", ""))[:600]
                    link    = getattr(entry, "link", "")
                    if source.get("ai_filter") and not _has_ai_keyword(title + " " + summary):
                        continue
                    extra.append({"source": source["name"], "title": title,
                                  "link": link, "summary": summary})
                    added += 1
                print(f"  🔍 Agent find_more: {source['name']} +{added} 条")
            except Exception as e:
                print(f"  ⚠ Agent find_more {source['name']} 失败: {e}")
        return {"status": f"补充抓取 {len(extra)} 篇文章", "extra_articles": extra}

    def _generate_special_report(self, topic: str) -> str:
        today = datetime.now().strftime("%Y-%m-%d")

        conn = self._get_conn()
        rows = conn.execute(
            "SELECT title, date, source FROM articles WHERE title LIKE ? ORDER BY date DESC LIMIT 20",
            (f"%{topic}%",),
        ).fetchall()
        conn.close()

        articles_text = "\n".join(
            f"- [{r['date']}] {r['title']} ({r['source']})" for r in rows
        ) or "（数据库中暂无相关记录，请结合知识分析）"

        client = anthropic.Anthropic()
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{"role": "user", "content": f"""请根据以下关于「{topic}」的新闻记录，生成一份中文深度专题分析报告（800字以内）：

{articles_text}

报告包含：背景介绍、最新进展、技术要点、行业影响四个部分。直接输出纯文本，每部分用标题行开头，不含 HTML 标签。"""}],
            )
            analysis = msg.content[0].text
        except Exception as e:
            analysis = f"专题分析生成失败: {e}"

        paragraphs = "".join(
            f"<p>{p.strip()}</p>" for p in analysis.split("\n") if p.strip()
        )

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>专题：{topic} · AI 导航</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;
  background:#0d1117;color:#e6edf3;line-height:1.8;max-width:800px;margin:0 auto;padding:2rem}}
h1{{font-size:1.5rem;color:#58a6ff;margin-bottom:.4rem}}
.meta{{color:#6e7681;font-size:.82rem;margin-bottom:2rem}}
p{{color:#c9d1d9;margin-bottom:.9rem}}
a{{color:#58a6ff;text-decoration:none}}
a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<a href="today.html">← 返回日报</a>
<h1>🔬 专题：{topic}</h1>
<div class="meta">生成时间：{today} · 由 Agent 自动生成</div>
{paragraphs}
</body>
</html>"""

        out_path = OUTPUT_DIR / f"special_{today}.html"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"  📋 专题报告已生成: {out_path.name}")
        return str(out_path)

    def _generate_topic_summary(self, topic: str) -> str:
        conn = self._get_conn()
        row  = conn.execute("SELECT * FROM topics WHERE name=?", (topic,)).fetchone()
        conn.close()

        if not row:
            return ""

        timeline = json.loads(row["timeline"] or "[]")
        tl_items = "".join(
            f'<div style="display:flex;gap:1rem;padding:.6rem 0;border-bottom:1px solid #21262d">'
            f'<span style="color:#6e7681;flex-shrink:0;width:5.5rem;font-size:.82rem">{ev.get("date","")}</span>'
            f'<span style="color:#c9d1d9;font-size:.88rem;line-height:1.6">{ev.get("summary","")}</span>'
            f'</div>'
            for ev in timeline
        ) or '<p style="color:#6e7681">暂无时间线记录</p>'

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>话题追踪：{topic} · AI 导航</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;
  background:#0d1117;color:#e6edf3;max-width:800px;margin:0 auto;padding:2rem}}
h1{{color:#58a6ff;font-size:1.5rem;margin-bottom:.4rem}}
.meta{{color:#6e7681;font-size:.82rem;margin-bottom:2rem}}
a{{color:#58a6ff;text-decoration:none}}
a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<a href="today.html">← 返回日报</a>
<h1>📌 话题追踪：{topic}</h1>
<div class="meta">
  首次出现：{row['first_seen']} ·
  最近：{row['last_seen']} ·
  已持续 <strong style="color:#58a6ff">{row['consecutive_days']}</strong> 天 ·
  共出现 {row['total_count']} 次
</div>
<div>{tl_items}</div>
</body>
</html>"""

        slug     = re.sub(r"[^\w一-鿿]", "_", topic)[:30]
        out_path = OUTPUT_DIR / f"topic_{slug}.html"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"  📌 话题时间线已生成: {out_path.name}")
        return str(out_path)

    # ── 反思阶段 ──────────────────────────────────────

    def reflect(self, decision: dict, final_count: int = 0, result: str = "") -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO agent_log(date, action, reason, targets, final_count, result, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                datetime.now().strftime("%Y-%m-%d"),
                decision.get("action", "normal"),
                decision.get("reason", ""),
                json.dumps(decision.get("targets", []), ensure_ascii=False),
                final_count,
                result or decision.get("_act_status", ""),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        conn.close()

    # ── 主入口 ──────────────────────────────────────

    def run(self) -> dict:
        print("  📡 感知阶段...")
        state = self.sense()
        print(
            f"     昨日文章: {state['yesterday_count']} 条 | "
            f"论文: {state['paper_count']} 篇 | "
            f"持续追踪话题: {len(state['long_running_topics'])} 个"
        )

        print("  🤔 判断阶段...")
        decision = self.think(state)
        print(f"     决策: [{decision.get('action')}] {decision.get('reason', '')}")

        print("  ⚡ 行动阶段...")
        act_result = self.act(decision)
        extra = act_result.get("extra_articles", [])
        if extra:
            print(f"     {act_result.get('status', '')}")

        decision["_extra_articles"] = extra
        decision["_act_status"]     = act_result.get("status", "")

        return decision
