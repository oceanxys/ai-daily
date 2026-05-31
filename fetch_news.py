#!/usr/bin/env python3
"""AI导航中心 - 多数据源抓取 + Claude 总结 + 多页静态生成（入口 + main orchestrator）。"""

import sys
import json
import time
from datetime import datetime
from pathlib import Path

from brain import Agent

from ai_daily.common import (
    Tee,
    _RUN_LOG,
    OUTPUT_DIR,
    INDEX_HTML_PATH,
    DATA_DIR,
)
from ai_daily.collectors import (
    fetch_news,
    fetch_model_versions,
    fetch_trending,
    fetch_benchmarks,
    fetch_tools,
    fetch_jobs,
    save_arxiv_papers,
)
from ai_daily.llm import (
    summarize_with_claude,
    generate_highlights,
    generate_topic_summaries_with_claude,
    detect_hot_topic,
    generate_embeddings,
)
from ai_daily.memory import (
    dedup_articles,
    save_articles_to_db,
    update_topics_db,
    get_persistent_topics,
    update_preferences_db,
)
from ai_daily.storage import (
    push_articles_to_cloud,
    push_topics_to_cloud,
    push_highlights_to_cloud,
    push_embeddings_to_cloud,
    save_archive,
    read_highlights,
)
from ai_daily.renderers import (
    _sort_categories_by_weight,
    _format_weight_log,
    generate_today_html,
    generate_models_html,
    generate_trending_html,
    generate_benchmark_html,
    generate_tools_html,
    generate_jobs_html,
    generate_index_html,
    generate_archive_html,
)


def main():
    log_path = Path.home() / "Projects" / "ai-daily" / "logs" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    sys.stdout = Tee(sys.stdout, log_file)
    sys.stderr = Tee(sys.stderr, log_file)

    # 注意：不能用 global _RUN_LOG 然后赋值（那会断开和 ai_daily.common 模块全局变量的引用关系）。
    # 改用 clear() + update() 原地 mutate，保持所有 from ai_daily.common import _RUN_LOG 拿到的引用仍指向同一对象。
    _RUN_LOG.clear()
    t0 = time.time()
    _RUN_LOG.update({
        "start_time":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sources":            {},
        "article_count":      0,
        "fallback_triggered": False,
        "hot_topic":          None,
        "warnings":           [],
    })

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
