#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 批量市场 ConditionId 获取器

功能：通过 Polygon Gamma 节点批量获取最近一周的市场的 conditionId

使用方法:
    python fetch_recent_markets.py --days 7 --limit 100 --output markets.json
    python fetch_recent_markets.py --days 7 --csv --output markets.csv
    python fetch_recent_markets.py --active-only --limit 50

示例:
    # 获取最近7天的前100个市场
    python fetch_recent_markets.py --days 7 --limit 100
    
    # 只获取活跃市场
    python fetch_recent_markets.py --active-only --limit 50
    
    # 导出为 CSV
    python fetch_recent_markets.py --days 7 --csv --output markets.csv
"""

import json
import sys
import argparse
import csv
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("Error: requests library not installed. Please install it with: pip install requests")
    sys.exit(1)


_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from data_sources import POLYMARKET_GAMMA_API_BASE


# Gamma API 基础 URL
GAMMA_API_BASE = POLYMARKET_GAMMA_API_BASE


def fetch_markets_batch(
    limit: int = 100,
    offset: int = 0,
    active_only: bool = False,
    closed_only: bool = False,
    order_by: str = "volume24hr",
    ascending: bool = False,
    timeout: int = 30
) -> List[Dict]:
    """
    从 Gamma API 批量获取市场
    
    Args:
        limit: 单次请求获取的市场数量（最大 100）
        offset: 偏移量（用于分页）
        active_only: 是否只获取活跃市场
        closed_only: 是否只获取已关闭市场
        order_by: 排序字段（volume24hr, endDate, createdAt 等）
        ascending: 是否升序
        timeout: 请求超时时间（秒）
    
    Returns:
        市场列表
    """
    url = f"{GAMMA_API_BASE}/markets"
    
    params = {
        'limit': min(limit, 100),  # API 限制单次最多 100
        'offset': offset,
        'order': order_by,
        'ascending': str(ascending).lower(),
    }
    
    # 设置活跃/关闭状态
    if active_only:
        params['active'] = 'true'
        params['closed'] = 'false'
    elif closed_only:
        params['active'] = 'false'
        params['closed'] = 'true'
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        
        # 解析响应（可能是数组或字典）
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            # 尝试不同的字段名
            for field in ['markets', 'data', 'results', 'items']:
                if field in data:
                    return data[field]
        
        return []
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching markets from Gamma API: {e}", file=sys.stderr)
        return []


def fetch_markets_by_date_range(
    days: int = 7,
    limit: int = 100,
    active_only: bool = False,
    order_by: str = "volume24hr"
) -> List[Dict]:
    """
    获取指定日期范围内的市场
    
    Args:
        days: 获取最近几天的市场
        limit: 最多获取的市场数量
        active_only: 是否只获取活跃市场
        order_by: 排序字段
    
    Returns:
        市场列表
    """
    start_date = datetime.now() - timedelta(days=days)
    
    print(f"🔄 正在获取最近 {days} 天的市场...")
    print(f"   起始日期: {start_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   活跃状态: {'仅活跃' if active_only else '全部'}")
    print()
    
    all_markets = []
    offset = 0
    batch_size = 100  # API 单次最大限制
    
    while len(all_markets) < limit:
        # 计算本次需要获取的数量
        remaining = limit - len(all_markets)
        current_batch_size = min(batch_size, remaining)
        
        print(f"📥 获取批次 {offset // batch_size + 1} (offset={offset}, limit={current_batch_size})...", end='')
        
        markets = fetch_markets_batch(
            limit=current_batch_size,
            offset=offset,
            active_only=active_only,
            order_by=order_by
        )
        
        if not markets:
            print(" ❌ 无数据")
            break
        
        print(f" ✅ 获取 {len(markets)} 个市场")
        
        # 过滤日期范围
        filtered_markets = []
        for market in markets:
            # 检查市场的创建时间或结束时间
            created_at = market.get('createdAt')
            end_date = market.get('endDate') or market.get('end_date')
            
            # 如果有创建时间，检查是否在范围内
            if created_at:
                try:
                    if isinstance(created_at, str):
                        clean_date = created_at.split('.')[0].split('+')[0].replace('Z', '').strip()
                        market_created = datetime.fromisoformat(clean_date)
                        
                        # 只保留最近几天创建的市场
                        if market_created >= start_date:
                            filtered_markets.append(market)
                            continue
                except Exception:
                    pass
            
            # 如果没有创建时间，检查结束时间（保留未来结束的市场）
            if end_date:
                try:
                    if isinstance(end_date, str):
                        clean_date = end_date.split('.')[0].split('+')[0].replace('Z', '').strip()
                        market_end = datetime.fromisoformat(clean_date)
                        
                        # 保留结束时间在未来或最近几天的市场
                        if market_end >= start_date:
                            filtered_markets.append(market)
                except Exception:
                    pass
        
        all_markets.extend(filtered_markets)
        
        # 如果这批没有符合条件的市场，或者已经到达末尾
        if not filtered_markets or len(markets) < current_batch_size:
            break
        
        offset += batch_size
    
    print()
    print(f"✅ 共获取 {len(all_markets)} 个符合条件的市场")
    
    return all_markets[:limit]


def extract_condition_ids(markets: List[Dict]) -> List[Dict]:
    """
    从市场数据中提取 conditionId 和相关信息
    
    Args:
        markets: 市场列表
    
    Returns:
        包含 conditionId 和关键信息的字典列表
    """
    results = []
    
    for market in markets:
        market_info = {
            'conditionId': market.get('conditionId') or market.get('condition_id'),
            'question': market.get('question'),
            'slug': market.get('slug'),
            'marketSlug': market.get('marketSlug'),
            'active': market.get('active'),
            'closed': market.get('closed'),
            'volume': market.get('volume') or market.get('volume24hr'),
            'liquidity': market.get('liquidity'),
            'outcomeTokens': market.get('tokens') or market.get('outcomeTokens'),
            'endDate': market.get('endDate') or market.get('end_date'),
            'createdAt': market.get('createdAt'),
        }
        
        # 如果有 token 信息，提取 token IDs
        tokens = market.get('tokens') or market.get('outcomeTokens')
        if tokens and isinstance(tokens, list):
            market_info['tokenIds'] = [token.get('tokenId') or token.get('token_id') for token in tokens]
            market_info['outcomes'] = [token.get('outcome') for token in tokens]
        
        results.append(market_info)
    
    return results


def save_to_json(data: List[Dict], filename: str):
    """保存为 JSON 文件"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"💾 数据已保存到: {filename}")


def save_to_csv(data: List[Dict], filename: str):
    """保存为 CSV 文件"""
    if not data:
        print("⚠️  没有数据可保存")
        return
    
    # 提取所有字段名（扁平化）
    fieldnames = set()
    for item in data:
        for key in item.keys():
            if not isinstance(item[key], (list, dict)):
                fieldnames.add(key)
    
    fieldnames = sorted(fieldnames)
    
    with open(filename, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for item in data:
            # 过滤掉复杂类型（list, dict）
            row = {k: v for k, v in item.items() if not isinstance(v, (list, dict))}
            writer.writerow(row)
    
    print(f"💾 数据已保存到: {filename}")


def print_summary(markets: List[Dict]):
    """打印摘要信息"""
    if not markets:
        print("⚠️  没有找到符合条件的市场")
        return
    
    print(f"\n{'='*60}")
    print(f"📊 市场摘要")
    print(f"{'='*60}")
    print(f"总市场数: {len(markets)}")
    
    # 统计活跃状态
    active_count = sum(1 for m in markets if m.get('active'))
    closed_count = sum(1 for m in markets if m.get('closed'))
    print(f"活跃市场: {active_count}")
    print(f"关闭市场: {closed_count}")
    
    # 统计有 conditionId 的市场
    with_condition_id = sum(1 for m in markets if m.get('conditionId'))
    print(f"有 conditionId: {with_condition_id}")
    
    print(f"\n{'='*60}")
    print(f"📝 前 5 个市场示例")
    print(f"{'='*60}")
    
    for i, market in enumerate(markets[:5], 1):
        print(f"\n{i}. {market.get('question', 'N/A')[:80]}")
        print(f"   conditionId: {market.get('conditionId', 'N/A')}")
        print(f"   slug: {market.get('slug', 'N/A')}")
        print(f"   状态: {'✅ 活跃' if market.get('active') else '❌ 关闭'}")
        
        # 处理 volume 可能是字符串或数字
        volume = market.get('volume')
        if volume:
            try:
                volume_float = float(volume)
                print(f"   交易量: ${volume_float:,.2f}")
            except (ValueError, TypeError):
                print(f"   交易量: {volume}")
        else:
            print("   交易量: N/A")


def main():
    parser = argparse.ArgumentParser(
        description='批量获取 Polymarket 市场的 conditionId',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 获取最近7天的前100个市场
  python fetch_recent_markets.py --days 7 --limit 100
  
  # 只获取活跃市场并导出为 JSON
  python fetch_recent_markets.py --active-only --limit 50 --output markets.json
  
  # 导出为 CSV
  python fetch_recent_markets.py --days 7 --csv --output markets.csv
  
  # 按创建时间排序
  python fetch_recent_markets.py --days 7 --order-by createdAt --limit 100
        """
    )
    
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='获取最近几天的市场（默认: 7）'
    )
    
    parser.add_argument(
        '--limit',
        type=int,
        default=100,
        help='最多获取的市场数量（默认: 100）'
    )
    
    parser.add_argument(
        '--active-only',
        action='store_true',
        help='只获取活跃市场'
    )
    
    parser.add_argument(
        '--closed-only',
        action='store_true',
        help='只获取已关闭市场'
    )
    
    parser.add_argument(
        '--order-by',
        type=str,
        default='volume24hr',
        choices=['volume24hr', 'endDate', 'createdAt', 'liquidity'],
        help='排序字段（默认: volume24hr）'
    )
    
    parser.add_argument(
        '--output',
        type=str,
        help='输出文件路径'
    )
    
    parser.add_argument(
        '--csv',
        action='store_true',
        help='以 CSV 格式输出（默认为 JSON）'
    )
    
    parser.add_argument(
        '--no-summary',
        action='store_true',
        help='不打印摘要信息'
    )
    
    args = parser.parse_args()
    
    # 获取市场数据
    markets = fetch_markets_by_date_range(
        days=args.days,
        limit=args.limit,
        active_only=args.active_only,
        order_by=args.order_by
    )
    
    if not markets:
        print("❌ 未获取到任何市场数据")
        return 1
    
    # 提取 conditionId 和关键信息
    market_data = extract_condition_ids(markets)
    
    # 打印摘要
    if not args.no_summary:
        print_summary(market_data)
    
    # 保存文件
    if args.output:
        if args.csv:
            save_to_csv(market_data, args.output)
        else:
            save_to_json(market_data, args.output)
    else:
        # 如果没有指定输出文件，生成默认文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        default_filename = f"polymarket_markets_{timestamp}.{'csv' if args.csv else 'json'}"
        
        if args.csv:
            save_to_csv(market_data, default_filename)
        else:
            save_to_json(market_data, default_filename)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
