"""Claude / Voyage AI API 调用：总结、JSON、精选、热词简报、热点检测、向量化。"""

import os
import re
import json
import time
from typing import Optional, Union

import anthropic
import voyageai


SYSTEM_PROMPT = """你是专业的AI行业分析师，负责整理每日AI资讯。
对每篇文章用中文撰写简洁摘要，突出技术价值与行业意义，语气客观专业。"""

MAX_ARTICLES = 30
BATCH_SIZE   = 10


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
        if not m:
            return None
        raw = m.group()

        def _clean(s: str) -> str:
            s = re.sub(r",\s*([\]\}])", r"\1", s)         # 去除对象/数组末尾多余逗号
            s = re.sub(r"'([^'\\]*)'\s*:", r'"\1":', s)   # 'key': → "key":
            s = re.sub(r":\s*'([^'\\]*)'", r': "\1"', s)  # : 'value' → : "value"
            return s

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  ⚠ Claude JSON 整体解析失败: {e}，尝试清理后重试")

        try:
            return json.loads(_clean(raw))
        except json.JSONDecodeError as e:
            print(f"  ⚠ Claude JSON 清理后仍失败: {e}")
            return None
    except Exception as e:
        print(f"  ⚠ Claude API 调用失败: {e}")
    return None


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
        if not m:
            print("  ⚠ 精选论文 Claude 返回未匹配到 JSON 数组")
            return []
        raw = m.group()

        def _clean(s: str) -> str:
            s = re.sub(r",\s*([\]\}])", r"\1", s)         # 去除对象/数组末尾多余逗号
            s = re.sub(r"'([^'\\]*)'\s*:", r'"\1":', s)   # 'key': → "key":
            s = re.sub(r":\s*'([^'\\]*)'", r': "\1"', s)  # : 'value' → : "value"
            return s

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  ⚠ JSON 整体解析失败: {e}，尝试清理后重试")

        try:
            return json.loads(_clean(raw))
        except json.JSONDecodeError as e:
            print(f"  ⚠ 清理后仍失败: {e}，尝试逐条解析")

        # 逐条解析：括号深度匹配抓出顶层 {...}，单独一条挂掉就跳过
        objs, depth, start, in_str, esc = [], 0, -1, False, False
        for i, ch in enumerate(raw):
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    objs.append(raw[start:i+1])
                    start = -1

        parsed = []
        for i, obj_text in enumerate(objs):
            try:
                parsed.append(json.loads(obj_text))
                continue
            except json.JSONDecodeError:
                pass
            try:
                parsed.append(json.loads(_clean(obj_text)))
            except json.JSONDecodeError as e2:
                print(f"  ⚠ 跳过第 {i+1} 条精选论文（解析失败：{e2}）")
        return parsed
    except Exception as e:
        print(f"  ⚠ 精选论文生成失败: {e}")
    return []


def generate_embeddings(articles: list, papers: list) -> list:
    print(f"🔢 生成 {len(articles)} 篇文章 + {len(papers)} 篇论文向量...")
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        print("  ⚠ VOYAGE_API_KEY 未配置，跳过向量化")
        return []

    items = []
    for a in articles:
        aid = a.get("link")
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
        pid = p.get("arxiv_url")
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
        print("  ⚠ 没有可向量化的内容（articles/papers 都缺少 id/source_id 或正文为空）")
        return []

    try:
        client = voyageai.Client(api_key=api_key)
        texts  = [it["content"] for it in items]
        # voyage-3-lite 单次最多 128 条，按批处理
        BATCH  = 128
        vectors = []
        for i in range(0, len(texts), BATCH):
            result = client.embed(texts[i:i+BATCH], model="voyage-3-lite", input_type="document")
            vectors.extend(result.embeddings)

        for it, vec in zip(items, vectors):
            it["embedding"] = vec

        print(f"  ✅ 生成 {len(items)} 条向量（{len([x for x in items if x['source_type']=='article'])} 篇文章 + {len([x for x in items if x['source_type']=='paper'])} 篇论文）")
        return items
    except Exception as e:
        print(f"  ⚠ 向量生成失败: {e}")
        return []
