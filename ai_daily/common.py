"""公共常量、路径、Tee、运行日志、通用工具函数。"""

import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# ── 输出路径 ──

OUTPUT_DIR          = Path.home() / "Projects" / "ai-daily" / "output"
ARCHIVE_DIR         = Path.home() / "Projects" / "ai-daily" / "archive"
OUTPUT_PATH         = OUTPUT_DIR / "today.html"
ARCHIVE_HTML_PATH   = OUTPUT_DIR / "archive.html"
INDEX_HTML_PATH     = OUTPUT_DIR / "index.html"
MODELS_HTML_PATH    = OUTPUT_DIR / "models.html"
TRENDING_HTML_PATH  = OUTPUT_DIR / "trending.html"
BENCHMARK_HTML_PATH = OUTPUT_DIR / "benchmark.html"
TOOLS_HTML_PATH     = OUTPUT_DIR / "tools.html"
JOBS_HTML_PATH      = OUTPUT_DIR / "jobs.html"
DATA_DIR            = Path.home() / "Projects" / "ai-daily" / "data"
HIGHLIGHTS_PATH     = DATA_DIR / "highlights.json"
PAPERS_PATH         = DATA_DIR / "papers_today.json"

# ── 云端 API 配置 ──
CLOUD_BASE      = "https://web-production-6e883.up.railway.app"
API_WRITE_TOKEN = os.environ.get("API_WRITE_TOKEN", "")
_AUTH_HEADERS   = {"Authorization": f"Bearer {API_WRITE_TOKEN}"} if API_WRITE_TOKEN else {}


# ── 日志双写工具 ──
class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()
    def flush(self):
        for s in self.streams:
            s.flush()


# ── 全局运行日志 ──
# 注意：这是一个跨模块共享的可变 dict。所有模块通过 `from ai_daily.common import _RUN_LOG`
# 拿到的都是同一个对象引用，可以直接 mutate。main() 入口会重新赋值（覆盖内容）。
_RUN_LOG: dict = {}


# ── HTTP 公共头 ──
_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ── 通用工具 ──

def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


AI_KEYWORDS = [
    "AI", "artificial intelligence", "machine learning", "deep learning",
    "GPT", "LLM", "large language model", "Claude", "OpenAI", "Anthropic",
    "neural network", "generative", "chatbot", "automation", "robot",
    "transformer", "diffusion", "multimodal",
]


def is_recent(entry, hours: int = 36) -> bool:
    """文章发布时间在最近 hours 小时内（宽松窗口，覆盖时区差异和跨日边界）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                pub = datetime(*parsed[:6], tzinfo=timezone.utc)
                return pub >= cutoff
            except Exception:
                pass
    return True  # 无时间戳的条目默认保留


def has_ai_keyword(entry) -> bool:
    text = (getattr(entry, "title", "") + " " + getattr(entry, "summary", "")).lower()
    return any(kw.lower() in text for kw in AI_KEYWORDS)
