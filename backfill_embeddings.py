#!/usr/bin/env python3
"""一次性历史数据向量化回灌

读 Railway PG 的 articles_cloud / papers 全量记录，过滤掉 embeddings 表里
已存在的，剩下的用 Voyage voyage-3-lite (input_type='document') 批量编码
并写回 embeddings（ON CONFLICT DO NOTHING）。

用法：
    source ~/.zshrc && python3 backfill_embeddings.py

环境变量：DATABASE_URL、VOYAGE_API_KEY
"""

import os
import sys
import json

import psycopg2
import psycopg2.extras
import voyageai

DATABASE_URL   = os.environ.get("DATABASE_URL")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY")
BATCH          = 100


def main():
    if not DATABASE_URL:
        print("⚠ DATABASE_URL 未配置（请确认已 source ~/.zshrc）")
        sys.exit(1)
    if not VOYAGE_API_KEY:
        print("⚠ VOYAGE_API_KEY 未配置（请确认已 source ~/.zshrc）")
        sys.exit(1)

    print("📡 连接 Railway PostgreSQL ...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        print("🔍 读取 embeddings 表已有记录 ...")
        cur.execute("SELECT source_type, source_id FROM embeddings")
        existing = {(r["source_type"], r["source_id"]) for r in cur.fetchall()}
        print(f"   当前 embeddings 表 {len(existing)} 条")

        print("📰 读取 articles_cloud ...")
        cur.execute("""
            SELECT id, chinese_title, chinese_summary, link, date, category
            FROM articles_cloud
        """)
        articles_rows = cur.fetchall()
        print(f"   共 {len(articles_rows)} 条")

        print("📄 读取 papers ...")
        cur.execute("""
            SELECT id, title, abstract, arxiv_url
            FROM papers
        """)
        papers_rows = cur.fetchall()
        print(f"   共 {len(papers_rows)} 条")

    items = []
    skipped_existing = 0
    skipped_empty    = 0

    for r in articles_rows:
        link = r.get("link")
        if not link:
            skipped_empty += 1
            continue
        if ("article", link) in existing:
            skipped_existing += 1
            continue
        text = f"{r.get('chinese_title') or ''} {r.get('chinese_summary') or ''}".strip()
        if not text:
            skipped_empty += 1
            continue
        items.append({
            "source_type": "article",
            "source_id":   link,
            "content":     text,
            "metadata": {
                "title":    r.get("chinese_title") or "",
                "date":     r.get("date") or "",
                "category": r.get("category") or "",
                "link":     link,
            },
        })

    for r in papers_rows:
        url = r.get("arxiv_url")
        if not url:
            skipped_empty += 1
            continue
        if ("paper", url) in existing:
            skipped_existing += 1
            continue
        text = f"{r.get('title') or ''} {r.get('abstract') or ''}".strip()
        if not text:
            skipped_empty += 1
            continue
        items.append({
            "source_type": "paper",
            "source_id":   url,
            "content":     text,
            "metadata": {
                "title":     r.get("title") or "",
                "arxiv_url": url,
            },
        })

    total = len(items)
    print(f"\n🚀 待回灌 {total} 条；已跳过 {skipped_existing} 条已存在 + {skipped_empty} 条空记录")

    if total == 0:
        print("✅ 无需回灌，embeddings 表已覆盖全量历史数据")
        conn.close()
        return

    client   = voyageai.Client(api_key=VOYAGE_API_KEY)
    inserted = 0
    failed   = 0

    with conn.cursor() as cur:
        for i in range(0, total, BATCH):
            batch    = items[i:i+BATCH]
            batch_to = min(i + BATCH, total)
            print(f"  正在处理第 {batch_to}/{total} 条 ...")
            try:
                result = client.embed(
                    [it["content"] for it in batch],
                    model="voyage-3-lite",
                    input_type="document",
                )
                for it, vec in zip(batch, result.embeddings):
                    cur.execute("""
                        INSERT INTO embeddings
                            (source_type, source_id, content, embedding, metadata)
                        VALUES (%s, %s, %s, %s::vector, %s)
                        ON CONFLICT (source_type, source_id) DO NOTHING
                    """, (
                        it["source_type"],
                        it["source_id"],
                        it["content"],
                        str(vec),
                        json.dumps(it["metadata"], ensure_ascii=False),
                    ))
                    inserted += 1
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"  ⚠ 第 {i+1}-{batch_to} 条失败: {e}")
                failed += len(batch)

    conn.close()
    print(f"\n✅ 完成！成功 {inserted} 条，失败 {failed} 条")
    print(f"   embeddings 表预计现有约 {len(existing) + inserted} 条")


if __name__ == "__main__":
    main()
