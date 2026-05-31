"""云端推送（Railway API）+ 本地存档 + highlights.json 读取。"""

import json
from datetime import datetime
from pathlib import Path

import httpx

from ai_daily.common import (
    SHANGHAI_TZ,
    CLOUD_BASE,
    _AUTH_HEADERS,
    ARCHIVE_DIR,
    HIGHLIGHTS_PATH,
)


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
                headers={"Content-Type": "application/json", **_AUTH_HEADERS},
            )
            if r.status_code == 200:
                d = r.json()
                print(f"  ✅ 云端入库：新增 {d.get('inserted', 0)} 篇，跳过重复 {len(articles) - d.get('inserted', 0)} 篇")
            else:
                print(f"  ⚠ 文章推送失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠ 文章推送到云端失败: {e}")


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
                headers={"Content-Type": "application/json", **_AUTH_HEADERS},
            )
            if r.status_code == 200:
                d = r.json()
                print(f"  ✅ 已推送 {d.get('topics_count', 0)} 个热词到云端")
            else:
                print(f"  ⚠ 热词推送失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠ 热词推送到云端失败: {e}")


def push_highlights_to_cloud(highlights: list) -> None:
    if not highlights:
        return
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(
                f"{CLOUD_BASE}/update_highlights",
                json=highlights,
                headers={"Content-Type": "application/json", **_AUTH_HEADERS},
            )
            if r.status_code == 200:
                print(f"  ✅ 已推送 {len(highlights)} 篇精选论文到云端")
            else:
                print(f"  ⚠ 精选论文推送失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ⚠ 精选论文推送到云端失败: {e}")


def push_embeddings_to_cloud(embeddings: list) -> None:
    if not embeddings:
        print("☁️ 没有向量可推送，跳过")
        return
    print(f"☁️ 推送 {len(embeddings)} 个向量到云端...")
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(
                f"{CLOUD_BASE}/update_embeddings",
                json=embeddings,
                headers={"Content-Type": "application/json", **_AUTH_HEADERS},
            )
            if r.status_code == 200:
                d = r.json()
                print(f"  ✅ 已推送 {d.get('upserted', len(embeddings))} 条向量到云端")
                print("✅ 向量化完成")
            else:
                print(f"  ⚠ 向量推送失败: HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  ⚠ 向量推送到云端失败: {e}")


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
