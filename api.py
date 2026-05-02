#!/usr/bin/env python3
"""AI Daily 本地 API 服务 - 端口 5001"""

import json
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATA_DIR       = Path.home() / "Projects" / "ai-daily" / "data"
PAPERS_PATH    = DATA_DIR / "papers_today.json"
HIGHLIGHTS_PATH = DATA_DIR / "highlights.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── GET /papers ──────────────────────────────────────────────
@app.route("/papers", methods=["GET"])
def get_papers():
    """返回今日抓取的论文列表。文件不存在时返回空列表。"""
    if not PAPERS_PATH.exists():
        return jsonify([])
    try:
        data = json.loads(PAPERS_PATH.read_text(encoding="utf-8"))
        # 兼容列表或 {"papers": [...]} 两种格式
        if isinstance(data, list):
            return jsonify(data)
        return jsonify(data.get("papers", []))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── POST /update_highlights ───────────────────────────────────
@app.route("/update_highlights", methods=["POST"])
def update_highlights():
    """
    接收扣子 Bot 返回的精选论文总结并写入 highlights.json。

    期望 JSON 格式（支持列表或对象）：
    [
      {
        "title":      "论文标题",
        "summary":    "一句话总结",
        "reason":     "推荐理由",
        "arxiv_link": "https://arxiv.org/abs/..."
      }
    ]
    或
    {
      "highlights": [ ... ]
    }
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "Content-Type 必须为 application/json"}), 400

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"success": False, "error": "无法解析 JSON"}), 400

    # 统一转为列表
    if isinstance(body, list):
        highlights = body
    elif isinstance(body, dict):
        highlights = body.get("highlights", [body] if body else [])
    else:
        return jsonify({"success": False, "error": "不支持的数据格式"}), 400

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


# ── 健康检查 ──────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
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
