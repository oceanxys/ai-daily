"""SQLite 本地记忆库：文章去重、话题追踪、分类偏好 EMA 权重。"""

import json
import difflib
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional


# ── CATEGORIES 与 renderers 共用：放在 common 之外的常量这里直接镜像一份 ──
# 但为避免重复定义，从 renderers 导入会触发循环引用，所以局部声明（顺序/内容必须与 renderers.CATEGORIES 完全一致）。
_CATEGORIES_FOR_PREF = [
    ("大模型动态",   "🤖", "#58a6ff"),
    ("AI产品与工具", "🛠️", "#3fb950"),
    ("AI研究进展",   "🔬", "#bc8cff"),
    ("AI商业动态",   "💰", "#f0883e"),
    ("AI政策与监管", "🌍", "#ff7b72"),
    ("其他",         "📌", "#8b949e"),
]

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
    for cat, _, _ in _CATEGORIES_FOR_PREF:
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
        for cat, _, _ in _CATEGORIES_FOR_PREF:
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
