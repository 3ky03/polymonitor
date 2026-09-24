#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 交易日志解码器 (Trade Decoder)

功能：解析 Polygon 链上的 Polymarket 交易日志，提取 OrderFilled 事件并解码为结构化 JSON

使用方法:
    python trade_decoder.py <tx_hash> [--rpc-url <url>] [--output <file.json>]

示例:
    python trade_decoder.py 0xfa0746b1...9198 --rpc-url "$POLYMARKET_RPC_URL" --output trade.json
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Any, List, Dict, Optional, Tuple
from decimal import Decimal, ROUND_DOWN

try:
    from web3 import Web3
    from web3.exceptions import TransactionNotFound
except ImportError:
    print("Error: web3 library not installed. Please install it with: pip install web3")
    sys.exit(1)

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from data_sources import POLYGON_RPC_URL


# Polymarket 交易所合约地址
CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # 普通二元市场 (CTF Exchange)
NEG_RISK_EXCHANGE_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"  # 负风险市场 (NegRisk CTF Exchange)
POLYMARKET_EXCHANGE_2026_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"  # 2026 新撮合合约
POLYMARKET_EXCHANGE_2026_ALT_ADDRESS = "0xe2222d279d744050d28e00520010520000310F59"  # 2026 新撮合合约（体育/新路由）

# OrderFilled 事件签名
# 事件签名: OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)
# 计算方式: keccak256("OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)")
# 注意：这里使用完整的事件签名字符串，Web3会自动计算哈希
ORDER_FILLED_EVENT_SIGNATURE = "OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)"
ORDER_FILLED_2026_EVENT_SIGNATURE = "OrderFilled(bytes32,address,address,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32)"

ORDER_FILLED_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "orderHash", "type": "bytes32"},
        {"indexed": True, "name": "maker", "type": "address"},
        {"indexed": True, "name": "taker", "type": "address"},
        {"indexed": False, "name": "makerAssetId", "type": "uint256"},
        {"indexed": False, "name": "takerAssetId", "type": "uint256"},
        {"indexed": False, "name": "makerAmountFilled", "type": "uint256"},
        {"indexed": False, "name": "takerAmountFilled", "type": "uint256"},
        {"indexed": False, "name": "fee", "type": "uint256"},
    ],
    "name": "OrderFilled",
    "type": "event",
}

ORDER_FILLED_2026_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "orderHash", "type": "bytes32"},
        {"indexed": True, "name": "maker", "type": "address"},
        {"indexed": True, "name": "taker", "type": "address"},
        {"indexed": False, "name": "side", "type": "uint8"},
        {"indexed": False, "name": "assetId", "type": "uint256"},
        {"indexed": False, "name": "amount0", "type": "uint256"},
        {"indexed": False, "name": "amount1", "type": "uint256"},
        {"indexed": False, "name": "fee", "type": "uint256"},
        {"indexed": False, "name": "makerOrderHash", "type": "bytes32"},
        {"indexed": False, "name": "takerOrderHash", "type": "bytes32"},
    ],
    "name": "OrderFilled",
    "type": "event",
}

# USDC 精度（6位小数）
USDC_DECIMALS = 6
USDC_DIVISOR = 10 ** USDC_DECIMALS

# 资产ID为0表示USDC
COLLATERAL_ASSET_ID = "0"
ZERO_ASSET_ID = "0"


def _hex_bytes(value: Any) -> str:
    if hasattr(value, "hex"):
        return value.hex()
    return str(value)


def _topic_hex(value: Any) -> str:
    text = _hex_bytes(value)
    return text if text.startswith("0x") else "0x" + text


def _topic_address(value: Any) -> str:
    raw = bytes(value) if not isinstance(value, str) else bytes.fromhex(value[2:] if value.startswith("0x") else value)
    return "0x" + raw[-20:].hex()


def _log_data_words(value: Any) -> List[bytes]:
    if isinstance(value, str):
        body = value[2:] if value.startswith("0x") else value
        raw = bytes.fromhex(body)
    else:
        raw = bytes(value)
    return [raw[i : i + 32] for i in range(0, len(raw), 32)]


def _word_int(word: bytes) -> int:
    return int.from_bytes(word, byteorder="big", signed=False)


def _word_hex(word: bytes) -> str:
    return "0x" + word.hex()


def is_collateral_asset(asset_id: str) -> bool:
    """判断资产ID是否为USDC（抵押品）"""
    return asset_id == COLLATERAL_ASSET_ID or asset_id == "0x0" or asset_id == "0x0000000000000000000000000000000000000000000000000000000000000000"


def get_order_filled_event_decoder(w3: Web3) -> Any:
    """复用 event decoder，避免每条日志都重复构造 ABI 对象。"""
    return w3.eth.contract(abi=[ORDER_FILLED_EVENT_ABI]).events.OrderFilled()


def get_order_filled_2026_event_decoder(w3: Web3) -> Any:
    """2026 新撮合合约 OrderFilled decoder。"""
    return w3.eth.contract(abi=[ORDER_FILLED_2026_EVENT_ABI]).events.OrderFilled()


def get_order_filled_event_decoders(w3: Web3) -> Dict[str, Any]:
    """返回按版本命名的 OrderFilled decoder。"""
    return {
        "legacy": get_order_filled_event_decoder(w3),
        "v2026": get_order_filled_2026_event_decoder(w3),
    }


def get_order_filled_topics(w3: Web3) -> Tuple[bytes, bytes]:
    return (
        w3.keccak(text=ORDER_FILLED_EVENT_SIGNATURE),
        w3.keccak(text=ORDER_FILLED_2026_EVENT_SIGNATURE),
    )


def is_supported_order_filled_log(log: Dict, w3: Web3) -> bool:
    """判断日志是否是当前支持的 Polymarket OrderFilled 事件。"""
    if not log.get("topics"):
        return False
    legacy_topic, topic_2026 = get_order_filled_topics(w3)
    topic0 = log["topics"][0]
    address = str(log.get("address") or "").lower()
    if topic0 == legacy_topic:
        return address in {
            CTF_EXCHANGE_ADDRESS.lower(),
            NEG_RISK_EXCHANGE_ADDRESS.lower(),
        }
    if topic0 == topic_2026:
        return address in {
            POLYMARKET_EXCHANGE_2026_ADDRESS.lower(),
            POLYMARKET_EXCHANGE_2026_ALT_ADDRESS.lower(),
        }
    return False


def decode_order_filled_log(
    log: Dict,
    w3: Optional[Web3] = None,
    event_decoder: Optional[Any] = None,
) -> Optional[Dict]:
    """
    解码 OrderFilled 事件日志
    
    Args:
        log: 日志对象
        w3: Web3 实例
    
    Returns:
        解码后的交易信息字典，如果解码失败返回 None
    """
    try:
        if event_decoder is None:
            if w3 is None:
                raise ValueError("decode_order_filled_log requires either w3 or event_decoder")
            event_decoder = get_order_filled_event_decoders(w3)

        if isinstance(event_decoder, dict):
            if w3 is not None:
                legacy_topic, topic_2026 = get_order_filled_topics(w3)
                topic0 = log["topics"][0] if log.get("topics") else None
                decoder_key = "v2026" if topic0 == topic_2026 else "legacy"
            else:
                log_address = str(log.get("address") or "").lower()
                decoder_key = (
                    "v2026"
                    if log_address
                    in {
                        POLYMARKET_EXCHANGE_2026_ADDRESS.lower(),
                        POLYMARKET_EXCHANGE_2026_ALT_ADDRESS.lower(),
                    }
                    else "legacy"
                )
            selected_decoder = event_decoder[decoder_key]
        else:
            selected_decoder = event_decoder

        # 解码日志
        decoded = selected_decoder.process_log(log)
        
        # 提取事件参数
        args = decoded['args']

        if "assetId" in args:
            return _decode_order_filled_2026(log, args)
        
        maker_asset_id = str(args['makerAssetId'])
        taker_asset_id = str(args['takerAssetId'])
        maker_amount = args['makerAmountFilled']
        taker_amount = args['takerAmountFilled']
        
        # 确定 tokenId（非USDC的资产ID）
        if is_collateral_asset(maker_asset_id):
            token_id = taker_asset_id
            usdc_amount = maker_amount
            token_amount = taker_amount
            side = "BUY"  # maker出USDC，买入token
        elif is_collateral_asset(taker_asset_id):
            token_id = maker_asset_id
            usdc_amount = taker_amount
            token_amount = maker_amount
            side = "SELL"  # taker出USDC，maker卖出token
        else:
            # 理论上不应该出现两个都是非零的情况，但处理一下
            print(f"Warning: Both assets are non-zero. Using takerAssetId as tokenId.")
            token_id = taker_asset_id
            usdc_amount = maker_amount  # 假设maker出USDC
            token_amount = taker_amount
            side = "BUY"
        
        # 计算价格：USDC数量 / Token数量
        # 注意：两个数量都需要除以1e6来归一化
        if token_amount > 0:
            price = Decimal(usdc_amount) / Decimal(token_amount)
            # 保留6位小数
            price = price.quantize(Decimal('0.000001'), rounding=ROUND_DOWN)
        else:
            price = Decimal('0')
        
        # 构建结果
        result = {
            "txHash": log['transactionHash'].hex(),
            "logIndex": log['logIndex'],
            "exchange": log['address'],
            "contract": log['address'],
            "orderHash": _hex_bytes(decoded['args']['orderHash']),
            "maker": args['maker'],
            "taker": args['taker'],
            "makerAssetId": maker_asset_id,
            "takerAssetId": taker_asset_id,
            "makerAmountFilled": str(maker_amount),
            "takerAmountFilled": str(taker_amount),
            "fee": str(args['fee']),
            "price": str(price),
            "tokenId": token_id,
            "side": side
        }
        
        return result
        
    except Exception as e:
        print(f"Error decoding log: {e}", file=sys.stderr)
        return None


def fast_decode_order_filled_log(log: Dict, w3: Web3) -> Optional[Dict]:
    """Fast path for supported OrderFilled logs.

    Web3's generic ABI event decoder is convenient but expensive when
    backfilling hundreds of thousands of logs per window. These events only
    contain static ABI types, so direct 32-byte word parsing is enough.
    """

    try:
        topics = list(log.get("topics") or [])
        if len(topics) < 4:
            return None
        legacy_topic, topic_2026 = get_order_filled_topics(w3)
        topic0 = topics[0]
        is_2026 = topic0 == topic_2026
        if topic0 != legacy_topic and not is_2026:
            return None

        words = _log_data_words(log.get("data") or b"")
        tx_hash = _hex_bytes(log["transactionHash"])
        order_hash = _topic_hex(topics[1])
        maker = _topic_address(topics[2])
        taker = _topic_address(topics[3])

        if is_2026:
            if len(words) < 7:
                return None
            side_code = _word_int(words[0])
            asset_id = str(_word_int(words[1]))
            amount0 = _word_int(words[2])
            amount1 = _word_int(words[3])
            fee = _word_int(words[4])
            maker_order_hash = _word_hex(words[5])
            taker_order_hash = _word_hex(words[6])

            if side_code == 0:
                side = "BUY"
                maker_asset_id = ZERO_ASSET_ID
                taker_asset_id = asset_id
                maker_amount = amount0
                taker_amount = amount1
                usdc_amount = amount0
                token_amount = amount1
            elif side_code == 1:
                side = "SELL"
                maker_asset_id = asset_id
                taker_asset_id = ZERO_ASSET_ID
                maker_amount = amount0
                taker_amount = amount1
                token_amount = amount0
                usdc_amount = amount1
            else:
                side = "UNKNOWN"
                maker_asset_id = asset_id
                taker_asset_id = ZERO_ASSET_ID
                maker_amount = amount0
                taker_amount = amount1
                token_amount = amount0
                usdc_amount = amount1

            price = (
                (Decimal(usdc_amount) / Decimal(token_amount)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
                if token_amount > 0
                else Decimal("0")
            )
            return {
                "txHash": tx_hash,
                "logIndex": log["logIndex"],
                "exchange": log["address"],
                "contract": log["address"],
                "orderHash": order_hash,
                "maker": maker,
                "taker": taker,
                "makerAssetId": maker_asset_id,
                "takerAssetId": taker_asset_id,
                "makerAmountFilled": str(maker_amount),
                "takerAmountFilled": str(taker_amount),
                "fee": str(fee),
                "price": str(price),
                "tokenId": asset_id,
                "side": side,
                "makerOrderHash": maker_order_hash,
                "takerOrderHash": taker_order_hash,
                "eventVersion": "v2026",
            }

        if len(words) < 5:
            return None
        maker_asset_id = str(_word_int(words[0]))
        taker_asset_id = str(_word_int(words[1]))
        maker_amount = _word_int(words[2])
        taker_amount = _word_int(words[3])
        fee = _word_int(words[4])

        if is_collateral_asset(maker_asset_id):
            token_id = taker_asset_id
            usdc_amount = maker_amount
            token_amount = taker_amount
            side = "BUY"
        elif is_collateral_asset(taker_asset_id):
            token_id = maker_asset_id
            usdc_amount = taker_amount
            token_amount = maker_amount
            side = "SELL"
        else:
            token_id = taker_asset_id
            usdc_amount = maker_amount
            token_amount = taker_amount
            side = "BUY"

        price = (
            (Decimal(usdc_amount) / Decimal(token_amount)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
            if token_amount > 0
            else Decimal("0")
        )
        return {
            "txHash": tx_hash,
            "logIndex": log["logIndex"],
            "exchange": log["address"],
            "contract": log["address"],
            "orderHash": order_hash,
            "maker": maker,
            "taker": taker,
            "makerAssetId": maker_asset_id,
            "takerAssetId": taker_asset_id,
            "makerAmountFilled": str(maker_amount),
            "takerAmountFilled": str(taker_amount),
            "fee": str(fee),
            "price": str(price),
            "tokenId": token_id,
            "side": side,
        }
    except Exception as exc:
        print(f"Error fast decoding log: {exc}", file=sys.stderr)
        return None


def _decode_order_filled_2026(log: Dict, args: Any) -> Dict:
    """解码 2026 新撮合合约 OrderFilled。

    新事件的 `side` 是 maker 视角：
    - 0: maker 用 USDC 买入 outcome token
    - 1: maker 卖出 outcome token 换 USDC
    """
    side_code = int(args["side"])
    asset_id = str(args["assetId"])
    amount0 = int(args["amount0"])
    amount1 = int(args["amount1"])
    fee = int(args["fee"])

    if side_code == 0:
        side = "BUY"
        maker_asset_id = ZERO_ASSET_ID
        taker_asset_id = asset_id
        maker_amount = amount0
        taker_amount = amount1
        usdc_amount = amount0
        token_amount = amount1
    elif side_code == 1:
        side = "SELL"
        maker_asset_id = asset_id
        taker_asset_id = ZERO_ASSET_ID
        maker_amount = amount0
        taker_amount = amount1
        token_amount = amount0
        usdc_amount = amount1
    else:
        side = "UNKNOWN"
        maker_asset_id = asset_id
        taker_asset_id = ZERO_ASSET_ID
        maker_amount = amount0
        taker_amount = amount1
        token_amount = amount0
        usdc_amount = amount1

    if token_amount > 0:
        price = Decimal(usdc_amount) / Decimal(token_amount)
        price = price.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    else:
        price = Decimal("0")

    return {
        "txHash": log["transactionHash"].hex(),
        "logIndex": log["logIndex"],
        "exchange": log["address"],
        "contract": log["address"],
        "orderHash": _hex_bytes(args["orderHash"]),
        "maker": args["maker"],
        "taker": args["taker"],
        "makerAssetId": maker_asset_id,
        "takerAssetId": taker_asset_id,
        "makerAmountFilled": str(maker_amount),
        "takerAmountFilled": str(taker_amount),
        "fee": str(fee),
        "price": str(price),
        "tokenId": asset_id,
        "side": side,
        "makerOrderHash": _hex_bytes(args["makerOrderHash"]),
        "takerOrderHash": _hex_bytes(args["takerOrderHash"]),
        "eventVersion": "v2026",
    }


def decode_transaction(tx_hash: str, rpc_url: str = POLYGON_RPC_URL) -> List[Dict]:
    """
    解码交易中的所有 OrderFilled 事件
    
    Args:
        tx_hash: 交易哈希
        rpc_url: Polygon RPC URL
    
    Returns:
        解码后的交易列表
    """
    # 连接到 Polygon
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")
    
    # 获取交易回执
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except TransactionNotFound:
        raise ValueError(f"Transaction not found: {tx_hash}")
    
    legacy_topic, topic_2026 = get_order_filled_topics(w3)
    
    # 过滤 OrderFilled 事件
    order_filled_logs = []
    
    # 构建交易所地址列表（用于过滤）
    legacy_exchange_addresses = [
        CTF_EXCHANGE_ADDRESS.lower(),
        NEG_RISK_EXCHANGE_ADDRESS.lower()
    ]
    
    for log in receipt['logs']:
        if len(log['topics']) <= 0:
            continue
        log_address = log['address'].lower()
        topic0 = log['topics'][0]
        if topic0 == legacy_topic and log_address in legacy_exchange_addresses:
            order_filled_logs.append(log)
        elif topic0 == topic_2026 and log_address in {
            POLYMARKET_EXCHANGE_2026_ADDRESS.lower(),
            POLYMARKET_EXCHANGE_2026_ALT_ADDRESS.lower(),
        }:
            order_filled_logs.append(log)
    
    # 解码所有 OrderFilled 日志
    event_decoder = get_order_filled_event_decoders(w3)
    decoded_trades = []
    for log in order_filled_logs:
        decoded = decode_order_filled_log(log, w3=w3, event_decoder=event_decoder)
        if decoded:
            decoded_trades.append(decoded)
    
    return decoded_trades


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket 交易日志解码器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python trade_decoder.py 0xfa0746b1...9198
  python trade_decoder.py 0xfa0746b1...9198 --rpc-url "$POLYMARKET_RPC_URL" --output trade.json
        """
    )
    
    parser.add_argument(
        "tx_hash",
        help="交易哈希（0x开头）"
    )
    
    parser.add_argument(
        "--rpc-url",
        default=POLYGON_RPC_URL,
        help="Polygon RPC URL (默认从 POLYMARKET_RPC_URL / NODE_URL 读取)"
    )
    
    parser.add_argument(
        "--output",
        "-o",
        help="输出JSON文件路径（如果不指定，输出到stdout）"
    )
    
    args = parser.parse_args()
    
    # 验证交易哈希格式
    if not args.tx_hash.startswith("0x"):
        args.tx_hash = "0x" + args.tx_hash
    
    if len(args.tx_hash) != 66:
        print(f"Error: Invalid transaction hash format: {args.tx_hash}", file=sys.stderr)
        sys.exit(1)
    
    try:
        # 解码交易
        print(f"Decoding transaction: {args.tx_hash}", file=sys.stderr)
        print(f"RPC URL: {args.rpc_url}", file=sys.stderr)
        
        decoded_trades = decode_transaction(args.tx_hash, args.rpc_url)
        
        if not decoded_trades:
            print("No OrderFilled events found in this transaction.", file=sys.stderr)
            sys.exit(1)
        
        # 准备输出
        if len(decoded_trades) == 1:
            output_data = decoded_trades[0]
        else:
            # 多条交易，输出数组
            output_data = {
                "txHash": args.tx_hash,
                "tradeCount": len(decoded_trades),
                "trades": decoded_trades
            }
        
        # 格式化JSON
        output_json = json.dumps(output_data, indent=2, ensure_ascii=False)
        
        # 输出
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(output_json)
            print(f"Results saved to: {args.output}", file=sys.stderr)
        else:
            print(output_json)
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
