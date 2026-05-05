#!/usr/bin/env python3
"""AI Daily 本地 API 服务 - 端口 5001"""

import json
import os
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATA_DIR        = Path.home() / "Projects" / "ai-daily" / "data"
PAPERS_PATH     = DATA_DIR / "papers_today.json"
HIGHLIGHTS_PATH = DATA_DIR / "highlights.json"
DATABASE_URL    = os.environ.get("DATABASE_URL")

DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    if not DATABASE_URL:
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS papers (
                        id          SERIAL PRIMARY KEY,
                        title       TEXT,
                        authors     TEXT,
                        abstract    TEXT,
                        arxiv_url   TEXT,
                        published   TEXT,
                        categories  TEXT,
                        created_at  TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS highlights (
                        id          SERIAL PRIMARY KEY,
                        title       TEXT,
                        summary     TEXT,
                        reason      TEXT,
                        arxiv_url   TEXT,
                        created_at  TIMESTAMP DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS topic_history (
                        id       SERIAL PRIMARY KEY,
                        keyword  TEXT NOT NULL,
                        heat     TEXT,
                        summary  TEXT,
                        date     TEXT NOT NULL,
                        count    INTEGER DEFAULT 1,
                        UNIQUE(keyword, date)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS topic_summaries (
                        keyword    TEXT PRIMARY KEY,
                        brief      TEXT,
                        background TEXT,
                        key_points TEXT,
                        sources    TEXT,
                        updated_at TEXT
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS articles_cloud (
                        id               SERIAL PRIMARY KEY,
                        title            TEXT,
                        chinese_title    TEXT,
                        chinese_summary  TEXT,
                        category         TEXT,
                        source           TEXT,
                        link             TEXT UNIQUE,
                        date             TEXT,
                        created_at       TIMESTAMP DEFAULT NOW()
                    )
                """)
            conn.commit()
        print("✅ 数据库表初始化完成")
    except Exception as e:
        print(f"⚠ 数据库初始化失败: {e}")


init_db()


# ── GET /papers ──────────────────────────────────────────────
@app.route("/papers", methods=["GET"])
def get_papers():
    today = datetime.now().strftime("%Y-%m-%d")

    if DATABASE_URL:
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT title, authors, abstract, arxiv_url, published, categories
                        FROM papers
                        WHERE created_at::date = CURRENT_DATE
                        ORDER BY id
                    """)
                    rows = cur.fetchall()
            papers = []
            for r in rows:
                authors_raw = json.loads(r["authors"] or "[]")
                papers.append({
                    "title":      r["title"],
                    "authors":    ", ".join(authors_raw) if isinstance(authors_raw, list) else authors_raw,
                    "abstract":   r["abstract"],
                    "arxiv_url":  r["arxiv_url"],
                    "published":  r["published"],
                    "categories": json.loads(r["categories"] or "[]"),
                })
            return jsonify({"papers": papers, "count": len(papers), "date": today})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    if not PAPERS_PATH.exists():
        return jsonify({"papers": [], "count": 0, "date": today})
    try:
        data = json.loads(PAPERS_PATH.read_text(encoding="utf-8"))
        raw_papers = data.get("papers", data) if isinstance(data, dict) else data
        papers = []
        for p in raw_papers:
            authors = p.get("authors", "")
            papers.append({**p, "authors": ", ".join(authors) if isinstance(authors, list) else authors})
        return jsonify({"papers": papers, "count": len(papers), "date": today})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── POST /update_highlights ───────────────────────────────────
@app.route("/update_highlights", methods=["POST"])
def update_highlights():
    if not request.is_json:
        return jsonify({"success": False, "error": "Content-Type 必须为 application/json"}), 400

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"success": False, "error": "无法解析 JSON"}), 400

    if isinstance(body, list):
        highlights = body
    elif isinstance(body, dict):
        highlights = body.get("highlights", [body] if body else [])
    else:
        return jsonify({"success": False, "error": "不支持的数据格式"}), 400

    if DATABASE_URL:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM highlights WHERE created_at::date = CURRENT_DATE")
                    for h in highlights:
                        cur.execute("""
                            INSERT INTO highlights (title, summary, reason, arxiv_url)
                            VALUES (%s, %s, %s, %s)
                        """, (
                            h.get("title", ""),
                            h.get("summary", ""),
                            h.get("reason", ""),
                            h.get("arxiv_link", h.get("arxiv_url", "")),
                        ))
                conn.commit()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    payload = {
        "highlights":  highlights,
        "updated_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count":       len(highlights),
    }
    HIGHLIGHTS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return jsonify({
        "success": True,
        "count":   len(highlights),
        "message": f"已写入 {len(highlights)} 篇精选论文",
    })


# ── POST /update_papers ──────────────────────────────────────
@app.route("/update_papers", methods=["POST"])
def update_papers():
    if not request.is_json:
        return jsonify({"success": False, "error": "Content-Type 必须为 application/json"}), 400

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"success": False, "error": "无法解析 JSON"}), 400

    papers = body.get("papers", []) if isinstance(body, dict) else body

    if DATABASE_URL:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM papers WHERE created_at::date = CURRENT_DATE")
                    for p in papers:
                        cur.execute("""
                            INSERT INTO papers (title, authors, abstract, arxiv_url, published, categories)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (
                            p.get("title", ""),
                            json.dumps(p.get("authors", []), ensure_ascii=False),
                            p.get("abstract", ""),
                            p.get("arxiv_url", ""),
                            p.get("published", ""),
                            json.dumps(p.get("categories", []), ensure_ascii=False),
                        ))
                conn.commit()
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAPERS_PATH.write_text(
        json.dumps(body, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    count = body.get("count", len(papers)) if isinstance(body, dict) else len(papers)
    return jsonify({
        "success": True,
        "count":   count,
        "message": f"已写入 {count} 篇论文",
    })


# ── GET /topics ───────────────────────────────────────────────
@app.route("/topics", methods=["GET"])
def get_topics():
    range_ = request.args.get("range", "today")

    if not DATABASE_URL:
        return jsonify({"topics": [], "range": range_})

    try:
        if range_ == "today":
            date_filter = "date = CURRENT_DATE::text"
        elif range_ == "week":
            date_filter = "date >= (CURRENT_DATE - INTERVAL '7 days')::text"
        else:
            date_filter = "date >= (CURRENT_DATE - INTERVAL '30 days')::text"

        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT keyword,
                           MAX(heat)    AS heat,
                           MAX(summary) AS summary,
                           SUM(count)   AS total_count,
                           MAX(date)    AS last_seen
                    FROM topic_history
                    WHERE {date_filter}
                    GROUP BY keyword
                    ORDER BY total_count DESC
                    LIMIT 30
                """)
                rows = cur.fetchall()

                cur.execute("SELECT keyword, count FROM topic_history WHERE date = CURRENT_DATE::text")
                today_counts = {r["keyword"]: r["count"] for r in cur.fetchall()}

                cur.execute("SELECT keyword, count FROM topic_history WHERE date = (CURRENT_DATE - INTERVAL '1 day')::text")
                yest_counts = {r["keyword"]: r["count"] for r in cur.fetchall()}

        topics = []
        for r in rows:
            kw      = r["keyword"]
            today_c = today_counts.get(kw, 0)
            yest_c  = yest_counts.get(kw, 0)
            if yest_c == 0 and today_c > 0:
                trend = "NEW"
            elif today_c > yest_c:
                trend = "up"
            elif today_c < yest_c:
                trend = "down"
            else:
                trend = "stable"
            topics.append({
                "keyword":   kw,
                "heat":      r["heat"],
                "summary":   r["summary"],
                "count":     r["total_count"],
                "last_seen": r["last_seen"],
                "trend":     trend,
            })

        return jsonify({"topics": topics, "range": range_})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── GET /topic/<keyword> ──────────────────────────────────────
@app.route("/topic/<path:keyword>", methods=["GET"])
def get_topic(keyword):
    if not DATABASE_URL:
        return jsonify({"keyword": keyword, "history": []})

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM topic_summaries WHERE keyword = %s", (keyword,))
                s = cur.fetchone()

                cur.execute("""
                    SELECT date, count, heat FROM topic_history
                    WHERE keyword = %s ORDER BY date ASC LIMIT 30
                """, (keyword,))
                history = [dict(r) for r in cur.fetchall()]

        if s:
            return jsonify({
                "keyword":    s["keyword"],
                "brief":      s["brief"],
                "background": s["background"],
                "key_points": json.loads(s["key_points"] or "[]"),
                "sources":    json.loads(s["sources"]    or "[]"),
                "updated_at": s["updated_at"],
                "history":    history,
            })
        return jsonify({"keyword": keyword, "history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── POST /update_topics ───────────────────────────────────────
@app.route("/update_topics", methods=["POST"])
def update_topics():
    if not request.is_json:
        return jsonify({"success": False, "error": "Content-Type 必须为 application/json"}), 400

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"success": False, "error": "无法解析 JSON"}), 400

    topics    = body.get("topics", [])
    summaries = body.get("summaries", [])

    if not DATABASE_URL:
        return jsonify({"success": True, "message": "文件模式，跳过写入"})

    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            with conn.cursor() as cur:
                for t in topics:
                    cur.execute("""
                        INSERT INTO topic_history (keyword, heat, summary, date, count)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (keyword, date) DO UPDATE SET
                            count   = topic_history.count + EXCLUDED.count,
                            heat    = EXCLUDED.heat,
                            summary = EXCLUDED.summary
                    """, (
                        t.get("keyword", ""),
                        t.get("heat", "热门"),
                        t.get("summary", ""),
                        t.get("date", datetime.now().strftime("%Y-%m-%d")),
                        t.get("count", 1),
                    ))
                for s in summaries:
                    cur.execute("""
                        INSERT INTO topic_summaries (keyword, brief, background, key_points, sources, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (keyword) DO UPDATE SET
                            brief      = EXCLUDED.brief,
                            background = EXCLUDED.background,
                            key_points = EXCLUDED.key_points,
                            sources    = EXCLUDED.sources,
                            updated_at = EXCLUDED.updated_at
                    """, (
                        s.get("keyword", ""),
                        s.get("brief", ""),
                        s.get("background", ""),
                        json.dumps(s.get("key_points", []), ensure_ascii=False),
                        json.dumps(s.get("sources",    []), ensure_ascii=False),
                        now_str,
                    ))
            conn.commit()

        return jsonify({
            "success":         True,
            "topics_count":    len(topics),
            "summaries_count": len(summaries),
            "message":         f"已写入 {len(topics)} 个热词，{len(summaries)} 条简报",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── GET /articles ────────────────────────────────────────────
@app.route("/articles", methods=["GET"])
def get_articles():
    date     = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    category = request.args.get("category")

    if not DATABASE_URL:
        return jsonify({"articles": [], "date": date})

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if category:
                    cur.execute("""
                        SELECT title, chinese_title, chinese_summary, category, source, link, date
                        FROM articles_cloud
                        WHERE date = %s AND category = %s
                        ORDER BY id
                    """, (date, category))
                else:
                    cur.execute("""
                        SELECT title, chinese_title, chinese_summary, category, source, link, date
                        FROM articles_cloud
                        WHERE date = %s
                        ORDER BY id
                    """, (date,))
                rows = [dict(r) for r in cur.fetchall()]
        return jsonify({"articles": rows, "count": len(rows), "date": date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── POST /update_articles ─────────────────────────────────────
@app.route("/update_articles", methods=["POST"])
def update_articles():
    if not request.is_json:
        return jsonify({"success": False, "error": "Content-Type 必须为 application/json"}), 400

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"success": False, "error": "无法解析 JSON"}), 400

    articles = body.get("articles", [])
    date     = body.get("date", datetime.now().strftime("%Y-%m-%d"))

    if not DATABASE_URL:
        return jsonify({"success": True, "message": "文件模式，跳过写入"})

    try:
        inserted = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                for a in articles:
                    cur.execute("""
                        INSERT INTO articles_cloud
                            (title, chinese_title, chinese_summary, category, source, link, date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (link) DO NOTHING
                    """, (
                        a.get("title", ""),
                        a.get("chinese_title", ""),
                        a.get("chinese_summary", ""),
                        a.get("category", "其他"),
                        a.get("source", ""),
                        a.get("link", ""),
                        date,
                    ))
                    if cur.rowcount:
                        inserted += 1
            conn.commit()
        return jsonify({"success": True, "inserted": inserted, "total": len(articles),
                        "message": f"新增 {inserted} 篇，跳过重复 {len(articles)-inserted} 篇"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── 全量统计 ──────────────────────────────────────────────────
@app.route("/stats", methods=["GET"])
def stats():
    if not DATABASE_URL:
        return jsonify({"error": "no database"}), 503
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM papers")
                papers_total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM highlights")
                highlights_total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM topic_history")
                topic_history_total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM topic_summaries")
                topic_summaries_total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM articles_cloud")
                articles_total = cur.fetchone()[0]
        return jsonify({
            "tables": {
                "papers":          papers_total,
                "highlights":      highlights_total,
                "topic_history":   topic_history_total,
                "topic_summaries": topic_summaries_total,
                "articles_cloud":  articles_total,
            },
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 健康检查 ──────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    if DATABASE_URL:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM papers WHERE created_at::date = CURRENT_DATE")
                    paper_count = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM highlights WHERE created_at::date = CURRENT_DATE")
                    highlight_count = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM topic_history WHERE date = CURRENT_DATE::text")
                    topic_count = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM articles_cloud WHERE created_at::date = CURRENT_DATE")
                    article_count = cur.fetchone()[0]
            return jsonify({
                "status":          "ok",
                "source":          "postgresql",
                "paper_count":     paper_count,
                "highlight_count": highlight_count,
                "topic_count":     topic_count,
                "article_count":   article_count,
                "time":            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    has_papers     = PAPERS_PATH.exists()
    has_highlights = HIGHLIGHTS_PATH.exists()
    highlight_count = 0
    if has_highlights:
        try:
            d = json.loads(HIGHLIGHTS_PATH.read_text(encoding="utf-8"))
            highlight_count = d.get("count", 0)
        except Exception:
            pass
    return jsonify({
        "status":          "ok",
        "source":          "file",
        "papers_file":     has_papers,
        "highlights_file": has_highlights,
        "highlight_count": highlight_count,
        "time":            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print("🚀 AI Daily API 服务启动")
    print(f"   GET  http://localhost:{port}/papers")
    print(f"   GET  http://localhost:{port}/topics")
    print(f"   GET  http://localhost:{port}/topic/<keyword>")
    print(f"   POST http://localhost:{port}/update_topics")
    print(f"   POST http://localhost:{port}/update_highlights")
    print(f"   GET  http://localhost:{port}/health")
    app.run(host="0.0.0.0", port=port, debug=False)
