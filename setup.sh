#!/bin/bash
set -e

echo "🔧 安装 AI 日报助手依赖..."

pip3 install feedparser anthropic

echo "✅ 依赖安装完成！"
echo ""
echo "运行前请确保已设置 ANTHROPIC_API_KEY 环境变量："
echo "  export ANTHROPIC_API_KEY='your-api-key'"
echo ""
echo "然后运行："
echo "  python fetch_news.py"
