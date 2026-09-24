#!/bin/bash
# Polymarket 工具集使用示例脚本

echo "==================================="
echo "Polymarket 工具集使用示例"
echo "==================================="
echo ""

# 示例 1: 获取最近 7 天的热门市场
echo "📊 示例 1: 获取最近 7 天的前 10 个活跃市场"
echo "命令: python market/fetch_recent_markets.py --days 7 --limit 10 --active-only --output ../database/markets.json"
echo ""
python market/fetch_recent_markets.py --days 7 --limit 10 --active-only --output ../database/markets.json
echo ""

# 示例 2: 从结果中提取第一个市场的 slug
if command -v jq &> /dev/null; then
    echo "📝 示例 2: 提取第一个市场的信息"
    slug=$(cat ../database/markets.json | jq -r '.[0].slug')
    condition_id=$(cat ../database/markets.json | jq -r '.[0].conditionId')
    question=$(cat ../database/markets.json | jq -r '.[0].question')
    
    echo "市场问题: $question"
    echo "Market Slug: $slug"
    echo "Condition ID: $condition_id"
    echo ""
    
    # 示例 3: 使用 market_decoder 解码该市场
    echo "🔍 示例 3: 解码市场参数"
    echo "命令: python market/market_decoder.py --gamma-slug $slug --verify"
    echo ""
    python market/market_decoder.py --gamma-slug "$slug" --verify
else
    echo "⚠️  jq 未安装，跳过 JSON 提取示例"
    echo "   安装: sudo apt-get install jq  或  brew install jq"
fi

echo ""
echo "==================================="
echo "✅ 示例演示完成！"
echo "==================================="
echo ""
echo "生成的文件:"
echo "  - database/markets.json (市场列表)"
echo ""
echo "更多用法请查看:"
echo "  - document/ (主文档与各脚本说明)"
