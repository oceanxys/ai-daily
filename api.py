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

    # fallback: JSON 文件
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

    # 同时写入 JSON 文件作为备份
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

    # 同时写入 JSON 文件作为备份
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
            return jsonify({
                "status":          "ok",
                "source":          "postgresql",
                "paper_count":     paper_count,
                "highlight_count": highlight_count,
                "time":            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    # fallback: 文件模式
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
    print(f"   POST http://localhost:{port}/update_highlights")
    print(f"   GET  http://localhost:{port}/health")
    app.run(host="0.0.0.0", port=port, debug=False)
