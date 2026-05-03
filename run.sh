#!/bin/bash
set -e

LOG_DIR="$HOME/Projects/ai-daily/logs"
LOG_FILE="$LOG_DIR/run.log"
API_LOG="$LOG_DIR/api.log"

mkdir -p "$LOG_DIR"

# ── 加载环境变量 ──
source "$HOME/.zshrc" 2>/dev/null || true

cd "$HOME/Projects/ai-daily" || exit 1

# ── 启动 api.py（如果未在运行）──
if lsof -ti :5001 > /dev/null 2>&1; then
  echo "ℹ  api.py 已在运行，跳过启动" | tee -a "$API_LOG"
else
  echo "▶ 启动 api.py ..." | tee -a "$API_LOG"
  nohup python3 api.py >> "$API_LOG" 2>&1 &
  API_PID=$!
  sleep 1
  if kill -0 "$API_PID" 2>/dev/null; then
    echo "✅ api.py 已启动 (PID: $API_PID)" | tee -a "$API_LOG"
  else
    echo "⚠  api.py 启动失败，查看 $API_LOG" | tee -a "$API_LOG"
    true  # 不因 api.py 失败而中断主流程
  fi
fi

# ── 运行 fetch_news.py（同时输出到终端和 run.log）──
{
  echo "=================================================="
  echo "▶ 开始运行: $(date '+%Y-%m-%d %H:%M:%S')"

  python3 fetch_news.py

  echo "✅ 运行完成: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "=================================================="
} 2>&1 | tee -a "$LOG_FILE"
