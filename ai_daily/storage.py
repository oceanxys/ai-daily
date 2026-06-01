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
    RUN_STATUS_PATH,
    _RUN_LOG,
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
                inserted = d.get("inserted", 0)
                _RUN_LOG.setdefault("pushed", {})["articles"] = inserted
                print(f"  ✅ 云端入库：新增 {inserted} 篇，跳过重复 {len(articles) - inserted} 篇")
            else:
                _RUN_LOG.setdefault("pushed", {})["articles"] = "failed"
                _RUN_LOG.setdefault("warnings", []).append(f"文章推送失败 HTTP {r.status_code}")
                print(f"  ⚠ 文章推送失败: HTTP {r.status_code}")
    except Exception as e:
        _RUN_LOG.setdefault("pushed", {})["articles"] = "failed"
        _RUN_LOG.setdefault("warnings", []).append(f"文章推送异常: {e}")
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
                cnt = d.get("topics_count", 0)
                _RUN_LOG.setdefault("pushed", {})["topics"] = cnt
                print(f"  ✅ 已推送 {cnt} 个热词到云端")
            else:
                _RUN_LOG.setdefault("pushed", {})["topics"] = "failed"
                _RUN_LOG.setdefault("warnings", []).append(f"热词推送失败 HTTP {r.status_code}")
                print(f"  ⚠ 热词推送失败: HTTP {r.status_code}")
    except Exception as e:
        _RUN_LOG.setdefault("pushed", {})["topics"] = "failed"
        _RUN_LOG.setdefault("warnings", []).append(f"热词推送异常: {e}")
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
                _RUN_LOG.setdefault("pushed", {})["highlights"] = len(highlights)
                print(f"  ✅ 已推送 {len(highlights)} 篇精选论文到云端")
            else:
                _RUN_LOG.setdefault("pushed", {})["highlights"] = "failed"
                _RUN_LOG.setdefault("warnings", []).append(f"精选论文推送失败 HTTP {r.status_code}")
                print(f"  ⚠ 精选论文推送失败: HTTP {r.status_code}")
    except Exception as e:
        _RUN_LOG.setdefault("pushed", {})["highlights"] = "failed"
        _RUN_LOG.setdefault("warnings", []).append(f"精选论文推送异常: {e}")
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
                upserted = d.get("upserted", len(embeddings))
                _RUN_LOG.setdefault("pushed", {})["embeddings"] = upserted
                print(f"  ✅ 已推送 {upserted} 条向量到云端")
                print("✅ 向量化完成")
            else:
                _RUN_LOG.setdefault("pushed", {})["embeddings"] = "failed"
                _RUN_LOG.setdefault("warnings", []).append(f"向量推送失败 HTTP {r.status_code}")
                print(f"  ⚠ 向量推送失败: HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        _RUN_LOG.setdefault("pushed", {})["embeddings"] = "failed"
        _RUN_LOG.setdefault("warnings", []).append(f"向量推送异常: {e}")
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


def _build_run_status_payload(quality_report: dict, filtered_count: int, run_log: dict) -> dict:
    """构建运行状态 payload，同时供 save_run_status（本地）和 push_run_status_to_cloud（云端）使用。"""
    return {
        "start_time":         run_log.get("start_time"),
        "end_time":           run_log.get("end_time"),
        "elapsed_seconds":    run_log.get("elapsed_seconds"),
        "hot_topic":          run_log.get("hot_topic"),
        "article_count":      run_log.get("article_count", 0),
        "fallback_triggered": run_log.get("fallback_triggered", False),
        "filtered_count":     filtered_count,
        "sources":            run_log.get("sources", {}),
        "quality_report":     quality_report or {},
        "pushed":             run_log.get("pushed", {}),
        "warnings":           run_log.get("warnings", []),
    }


def save_run_status(quality_report: dict, filtered_count: int, run_log: dict) -> None:
    """把最近一次运行的关键状态落盘到 data/run_status.json，供 status.html 渲染和外部消费者使用。"""
    payload = _build_run_status_payload(quality_report, filtered_count, run_log)
    RUN_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUN_STATUS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✅ 运行状态已保存: {RUN_STATUS_PATH}")


def push_run_status_to_cloud(quality_report: dict, filtered_count: int, run_log: dict) -> None:
    """推送运行状态到 Railway，写入 run_status 表，供扣子 Agent 等外部消费者查询。

    注意：本函数自身的失败 **不** 写回 _RUN_LOG.warnings（避免把"状态推送失败"再写进状态本身），
    只 print + 让 launchd 日志捕获。
    """
    payload = _build_run_status_payload(quality_report, filtered_count, run_log)
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(
                f"{CLOUD_BASE}/update_run_status",
                json=payload,
                headers={"Content-Type": "application/json", **_AUTH_HEADERS},
            )
            if r.status_code == 200:
                d = r.json()
                print(f"  ✅ 运行状态已推送到云端 (id={d.get('id', '?')})")
            else:
                print(f"  ⚠ 运行状态推送失败: HTTP {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  ⚠ 运行状态推送到云端失败: {e}")
