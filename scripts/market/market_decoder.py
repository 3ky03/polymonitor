
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 市场参数解码器 (Market Decoder)

功能：根据 conditionId 或 ConditionPreparation 事件日志，计算市场的核心链上参数，
包括预言机、问题ID、抵押品地址，以及 YES/NO 两种头寸的 TokenId。

使用方法:
    python market_decoder.py --condition-id <conditionId> --oracle <oracle> --question-id <questionId>
    python market_decoder.py --tx-hash <tx_hash> --log-index <log_index> [--rpc-url <url>]
    python market_decoder.py --gamma-slug <slug> [--verify]

示例:
    python market_decoder.py --condition-id 0xabc123... --oracle 0xOracle... --question-id 0xdef456...
    python market_decoder.py --tx-hash 0x123... --log-index 5 --rpc-url "$POLYMARKET_RPC_URL"
    python market_decoder.py --gamma-slug fed-rate-jan2024 --verify
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    from web3 import Web3
    from web3.exceptions import TransactionNotFound
except ImportError:
    print("Error: web3 library not installed. Please install it with: pip install web3")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Error: requests library not installed. Please install it with: pip install requests")
    sys.exit(1)

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from data_sources import POLYGON_RPC_URL, POLYMARKET_GAMMA_API_BASE


# Polymarket 常量
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # CTF 合约地址（Polygon）

# ConditionPreparation 事件签名
CONDITION_PREPARATION_EVENT_SIGNATURE = "ConditionPreparation(bytes32,address,bytes32,uint256)"

# Gamma API 基础 URL
GAMMA_API_BASE = POLYMARKET_GAMMA_API_BASE


def keccak256(data: bytes) -> bytes:
    """计算 keccak256 哈希"""
    return Web3.keccak(data)


def solidity_pack(types: list, values: list) -> bytes:
    """
    模拟 Solidity 的 abi.encodePacked
    注意：Python 的 abi.encode 和 encodePacked 不同，这里需要手动实现
    """
    result = b""
    for typ, val in zip(types, values):
        if typ == "bytes32":
            if isinstance(val, str):
                if val.startswith("0x"):
                    val = bytes.fromhex(val[2:])
                else:
                    val = bytes.fromhex(val)
            elif isinstance(val, int):
                val = val.to_bytes(32, byteorder='big')
            result += val
        elif typ == "address":
            if isinstance(val, str):
                if val.startswith("0x"):
                    val = bytes.fromhex(val[2:])
                else:
                    val = bytes.fromhex(val)
            # 地址是 20 字节，但需要补齐到 32 字节（对于 encodePacked 实际上不需要）
            # 但为了匹配 Solidity，我们直接使用 20 字节
            if len(val) == 32:
                val = val[12:]  # 去掉前导零
            result += val
        elif typ == "uint256" or typ == "uint":
            if isinstance(val, str):
                val = int(val, 16) if val.startswith("0x") else int(val)
            val_bytes = val.to_bytes(32, byteorder='big')
            result += val_bytes
        else:
            raise ValueError(f"Unsupported type: {typ}")
    return result


def calculate_condition_id(oracle: str, question_id: str, outcome_slot_count: int = 2) -> str:
    """
    计算 conditionId
    
    conditionId = keccak256(abi.encodePacked(oracle, questionId, outcomeSlotCount))
    """
    # 确保地址格式正确（去掉 0x，补齐到 20 字节）
    if oracle.startswith("0x"):
        oracle_bytes = bytes.fromhex(oracle[2:])
    else:
        oracle_bytes = bytes.fromhex(oracle)
    
    # questionId 是 bytes32
    if question_id.startswith("0x"):
        question_id_bytes = bytes.fromhex(question_id[2:])
    else:
        question_id_bytes = bytes.fromhex(question_id)
    
    # outcomeSlotCount 是 uint256
    outcome_bytes = outcome_slot_count.to_bytes(32, byteorder='big')
    
    # abi.encodePacked
    packed = oracle_bytes + question_id_bytes + outcome_bytes
    
    # keccak256
    condition_id = keccak256(packed)
    
    return "0x" + condition_id.hex()


def calculate_collection_id(
    parent_collection_id: str,
    condition_id: str,
    index_set: int,
    w3: Optional[Web3] = None,
    ctf_address: Optional[str] = None
) -> str:
    """
    计算 CollectionId
    
    注意：CTF 的完整实现涉及椭圆曲线运算，非常复杂。
    对于 Polymarket（parentCollectionId = 0），文档中描述的简化版本是：
    collectionId = keccak256(abi.encodePacked(parentCollectionId, conditionId, indexSet))
    
    但如果需要精确结果，应该调用链上合约的 getCollectionId 方法。
    
    Args:
        parent_collection_id: 父集合ID（Polymarket 总是 bytes32(0)）
        condition_id: 条件ID
        index_set: 索引集合（1=YES, 2=NO）
        w3: Web3 实例（可选，用于调用链上合约）
        ctf_address: ConditionalTokens 合约地址（可选）
    
    Returns:
        CollectionId (bytes32 格式的十六进制字符串)
    """
    # 如果提供了 Web3 和合约地址，尝试调用链上合约获取精确结果
    if w3 and ctf_address:
        try:
            # ConditionalTokens 合约 ABI（只需要 getCollectionId 方法）
            ctf_abi = [
                {
                    "inputs": [
                        {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
                        {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
                        {"internalType": "uint256", "name": "indexSet", "type": "uint256"}
                    ],
                    "name": "getCollectionId",
                    "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
                    "stateMutability": "view",
                    "type": "function"
                }
            ]
            
            ctf_contract = w3.eth.contract(address=Web3.to_checksum_address(ctf_address), abi=ctf_abi)
            
            parent_bytes32 = bytes.fromhex(parent_collection_id[2:] if parent_collection_id.startswith("0x") else parent_collection_id)
            condition_bytes32 = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
            
            collection_id = ctf_contract.functions.getCollectionId(
                parent_bytes32,
                condition_bytes32,
                index_set
            ).call()
            
            return "0x" + collection_id.hex()
        except Exception as e:
            print(f"Warning: Failed to call on-chain getCollectionId: {e}", file=sys.stderr)
            print("Falling back to simplified calculation...", file=sys.stderr)
    
    # 简化版本：使用文档中描述的公式
    # 注意：这可能在 parentCollectionId != 0 时不够精确
    if parent_collection_id.startswith("0x"):
        parent_bytes = bytes.fromhex(parent_collection_id[2:])
    else:
        parent_bytes = bytes.fromhex(parent_collection_id)
    
    if condition_id.startswith("0x"):
        condition_bytes = bytes.fromhex(condition_id[2:])
    else:
        condition_bytes = bytes.fromhex(condition_id)
    
    index_set_bytes = index_set.to_bytes(32, byteorder='big')
    
    # abi.encodePacked: 直接拼接字节
    packed = parent_bytes + condition_bytes + index_set_bytes
    collection_id = keccak256(packed)
    
    return "0x" + collection_id.hex()


def calculate_position_id(
    collateral_token: str,
    collection_id: str,
    w3: Optional[Web3] = None,
    ctf_address: Optional[str] = None
) -> str:
    """
    计算 PositionId (TokenId)
    
    positionId = keccak256(abi.encodePacked(collateralToken, collectionId))
    然后转换为 uint256
    
    如果提供了 Web3 和合约地址，可以调用链上合约验证
    """
    # 如果提供了 Web3 和合约地址，尝试调用链上合约获取精确结果
    if w3 and ctf_address:
        try:
            ctf_abi = [
                {
                    "inputs": [
                        {"internalType": "address", "name": "collateralToken", "type": "address"},
                        {"internalType": "bytes32", "name": "collectionId", "type": "bytes32"}
                    ],
                    "name": "getPositionId",
                    "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
                    "stateMutability": "pure",
                    "type": "function"
                }
            ]
            
            ctf_contract = w3.eth.contract(address=Web3.to_checksum_address(ctf_address), abi=ctf_abi)
            
            collection_bytes32 = bytes.fromhex(collection_id[2:] if collection_id.startswith("0x") else collection_id)
            
            position_id = ctf_contract.functions.getPositionId(
                Web3.to_checksum_address(collateral_token),
                collection_bytes32
            ).call()
            
            return str(position_id)
        except Exception as e:
            print(f"Warning: Failed to call on-chain getPositionId: {e}", file=sys.stderr)
            print("Falling back to local calculation...", file=sys.stderr)
    
    # 本地计算
    # 地址转换为 bytes（20 字节）
    if collateral_token.startswith("0x"):
        token_bytes = bytes.fromhex(collateral_token[2:])
    else:
        token_bytes = bytes.fromhex(collateral_token)
    
    # collectionId 是 bytes32
    if collection_id.startswith("0x"):
        collection_bytes = bytes.fromhex(collection_id[2:])
    else:
        collection_bytes = bytes.fromhex(collection_id)
    
    # abi.encodePacked: 直接拼接
    packed = token_bytes + collection_bytes
    position_id_hash = keccak256(packed)
    
    # 转换为 uint256（大整数）
    position_id_int = int.from_bytes(position_id_hash, byteorder='big')
    
    return str(position_id_int)


def decode_condition_preparation_log(log: Dict, w3: Web3) -> Optional[Dict]:
    """
    解码 ConditionPreparation 事件日志
    
    Args:
        log: 日志对象
        w3: Web3 实例
    
    Returns:
        解码后的事件参数，如果解码失败返回 None
    """
    try:
        event_abi = {
            "anonymous": False,
            "inputs": [
                {"indexed": True, "name": "conditionId", "type": "bytes32"},
                {"indexed": True, "name": "oracle", "type": "address"},
                {"indexed": True, "name": "questionId", "type": "bytes32"},
                {"indexed": False, "name": "outcomeSlotCount", "type": "uint256"}
            ],
            "name": "ConditionPreparation",
            "type": "event"
        }
        
        event = w3.eth.contract(abi=[event_abi]).events.ConditionPreparation()
        decoded = event.process_log(log)
        
        args = decoded['args']
        
        return {
            "conditionId": args['conditionId'].hex(),
            "oracle": args['oracle'],
            "questionId": args['questionId'].hex(),
            "outcomeSlotCount": args['outcomeSlotCount']
        }
        
    except Exception as e:
        print(f"Error decoding ConditionPreparation log: {e}", file=sys.stderr)
        return None


def get_oracle_from_condition_id(
    condition_id: str,
    rpc_url: str = POLYGON_RPC_URL,
    ctf_address: Optional[str] = None
) -> Optional[str]:
    """
    从链上查询 ConditionPreparation 事件来获取 oracle 地址
    
    Args:
        condition_id: 条件ID
        rpc_url: RPC URL
        ctf_address: ConditionalTokens 合约地址
    
    Returns:
        Oracle 地址，如果查询失败返回 None
    """
    if ctf_address is None:
        ctf_address = CONDITIONAL_TOKENS_ADDRESS
    
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        return None
    
    try:
        # 创建事件过滤器
        event_abi = {
            "anonymous": False,
            "inputs": [
                {"indexed": True, "name": "conditionId", "type": "bytes32"},
                {"indexed": True, "name": "oracle", "type": "address"},
                {"indexed": True, "name": "questionId", "type": "bytes32"},
                {"indexed": False, "name": "outcomeSlotCount", "type": "uint256"}
            ],
            "name": "ConditionPreparation",
            "type": "event"
        }
        
        contract = w3.eth.contract(address=Web3.to_checksum_address(ctf_address), abi=[event_abi])
        event = contract.events.ConditionPreparation()
        
        # 查询最近的 ConditionPreparation 事件（最多查询最近1000个区块）
        latest_block = w3.eth.block_number
        from_block = max(0, latest_block - 100000)  # 查询最近10万个区块
        
        # 使用事件过滤器查询
        condition_id_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
        events = event.get_logs(
            argument_filters={"conditionId": condition_id_bytes},
            fromBlock=from_block,
            toBlock=latest_block
        )
        
        if events and len(events) > 0:
            # 返回第一个匹配事件的 oracle
            return events[0]['args']['oracle']
        
        return None
    except Exception as e:
        print(f"Warning: Failed to query oracle from chain: {e}", file=sys.stderr)
        return None


def decode_market_from_log(
    tx_hash: str,
    log_index: int,
    rpc_url: str = POLYGON_RPC_URL,
    ctf_address: Optional[str] = None
) -> Optional[Dict]:
    """
    从交易日志中解码市场信息
    
    Args:
        tx_hash: 交易哈希
        log_index: 日志索引
        rpc_url: RPC URL
    
    Returns:
        市场信息字典
    """
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")
    
    # 如果没有提供 ctf_address，使用默认值
    if ctf_address is None:
        ctf_address = CONDITIONAL_TOKENS_ADDRESS
    
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except TransactionNotFound:
        raise ValueError(f"Transaction not found: {tx_hash}")
    
    # 查找指定索引的日志
    target_log = None
    for log in receipt['logs']:
        if log['logIndex'] == log_index:
            target_log = log
            break
    
    if not target_log:
        raise ValueError(f"Log with index {log_index} not found in transaction {tx_hash}")
    
    # 解码 ConditionPreparation 事件
    event_params = decode_condition_preparation_log(target_log, w3)
    
    if not event_params:
        raise ValueError("Failed to decode ConditionPreparation event")
    
    return calculate_market_tokens(
        event_params['conditionId'],
        event_params['oracle'],
        event_params['questionId'],
        event_params['outcomeSlotCount'],
        w3=w3,
        ctf_address=ctf_address
    )


def calculate_market_tokens(
    condition_id: str,
    oracle: str,
    question_id: str,
    outcome_slot_count: int = 2,
    collateral_token: str = USDC_E_ADDRESS,
    w3: Optional[Web3] = None,
    ctf_address: Optional[str] = None
) -> Dict:
    """
    计算市场的 TokenId
    
    Args:
        condition_id: 条件ID
        oracle: 预言机地址
        question_id: 问题ID
        outcome_slot_count: 结果数量（默认2）
        collateral_token: 抵押品代币地址（默认USDC.e）
    
    Returns:
        包含所有市场参数的字典
    """
    # 验证 conditionId（可选，如果提供的话）
    calculated_condition_id = calculate_condition_id(oracle, question_id, outcome_slot_count)
    if condition_id.lower() != calculated_condition_id.lower():
        print(f"Warning: Provided conditionId {condition_id} does not match calculated {calculated_condition_id}", file=sys.stderr)
        print(f"Using provided conditionId: {condition_id}", file=sys.stderr)
    
    # parentCollectionId 对于 Polymarket 总是 bytes32(0)
    parent_collection_id = "0x" + "0" * 64
    
    # 计算 CollectionId
    # YES: indexSet = 1 (0b01)
    collection_id_yes = calculate_collection_id(parent_collection_id, condition_id, 1, w3, ctf_address)
    
    # NO: indexSet = 2 (0b10)
    collection_id_no = calculate_collection_id(parent_collection_id, condition_id, 2, w3, ctf_address)
    
    # 计算 PositionId (TokenId)
    yes_token_id = calculate_position_id(collateral_token, collection_id_yes, w3, ctf_address)
    no_token_id = calculate_position_id(collateral_token, collection_id_no, w3, ctf_address)
    
    return {
        "conditionId": condition_id,
        "questionId": question_id,
        "oracle": oracle,
        "collateralToken": collateral_token,
        "outcomeSlotCount": outcome_slot_count,
        "yesTokenId": yes_token_id,
        "noTokenId": no_token_id,
        "yesCollectionId": collection_id_yes,
        "noCollectionId": collection_id_no
    }


def fetch_gamma_event(slug: str) -> Optional[Dict]:
    """
    从 Gamma API 获取事件信息
    
    Args:
        slug: 事件 slug
    
    Returns:
        事件信息字典，如果获取失败返回 None
    """
    try:
        url = f"{GAMMA_API_BASE}/events"
        params = {"slug": slug}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        events = response.json()
        if events and len(events) > 0:
            return events[0]  # 返回第一个匹配的事件
        return None
    except Exception as e:
        print(f"Error fetching event from Gamma API: {e}", file=sys.stderr)
        return None


def fetch_gamma_market(slug: str, market_index: Optional[int] = None, _is_recursive: bool = False) -> Optional[Dict]:
    """
    从 Gamma API 获取市场信息
    
    首先尝试作为市场 slug 查询，如果失败则尝试作为事件 slug 查询。
    如果是事件 slug，可以从事件的 markets 数组中提取市场。
    
    Args:
        slug: 市场 slug 或事件 slug
        market_index: 如果是事件 slug，指定要使用的市场索引（默认使用第一个）
        _is_recursive: 内部标志，防止无限递归
    
    Returns:
        市场信息字典，如果获取失败返回 None
    """
    # 首先尝试作为市场 slug 查询（使用查询参数）
    try:
        url = f"{GAMMA_API_BASE}/markets"
        params = {"slug": slug}
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        market_data = response.json()
        # 如果返回的是数组，取第一个
        if isinstance(market_data, list) and len(market_data) > 0:
            return market_data[0]
        # 如果返回的是单个市场对象
        if isinstance(market_data, dict) and 'conditionId' in market_data:
            return market_data
        # 如果返回空数组，继续尝试作为事件 slug
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404 or e.response.status_code == 422:
            # 404/422 表示不是市场 slug，尝试作为事件 slug
            pass
        else:
            print(f"Error fetching market from Gamma API: {e}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"Error fetching market from Gamma API: {e}", file=sys.stderr)
        return None
    
    # 如果不是市场 slug，尝试作为事件 slug（但避免递归调用）
    if _is_recursive:
        print(f"Error: '{slug}' is not a valid market slug", file=sys.stderr)
        return None
    
    print(f"'{slug}' is not a market slug, trying as event slug...", file=sys.stderr)
    event_data = fetch_gamma_event(slug)
    
    if not event_data:
        print(f"Error: '{slug}' is neither a valid market slug nor event slug", file=sys.stderr)
        return None
    
    # 从事件中提取市场
    markets = event_data.get('markets', [])
    if not markets:
        print(f"Error: Event '{slug}' has no markets", file=sys.stderr)
        return None
    
    # 如果指定了市场索引
    if market_index is not None:
        if market_index < 0 or market_index >= len(markets):
            print(f"Error: Market index {market_index} is out of range (0-{len(markets)-1})", file=sys.stderr)
            return None
        selected_market = markets[market_index]
    else:
        # 默认使用第一个市场
        selected_market = markets[0]
        if len(markets) > 1:
            print(f"Info: Event '{slug}' has {len(markets)} markets, using the first one:", file=sys.stderr)
            for i, m in enumerate(markets[:5]):  # 最多显示5个
                print(f"  [{i}] {m.get('slug', 'N/A')} - {m.get('question', 'N/A')[:60]}...", file=sys.stderr)
            if len(markets) > 5:
                print(f"  ... and {len(markets) - 5} more markets", file=sys.stderr)
            print(f"  Use --market-index <N> to select a different market", file=sys.stderr)
    
    # 获取完整的市场信息（使用市场的 slug）
    market_slug = selected_market.get('slug')
    if not market_slug:
        print(f"Error: Selected market has no slug", file=sys.stderr)
        return None
    
    # 递归调用获取完整的市场数据（设置递归标志）
    return fetch_gamma_market(market_slug, _is_recursive=True)


def verify_with_gamma(market_data: Dict, slug: str) -> bool:
    """
    使用 Gamma API 验证计算结果
    
    Args:
        market_data: 计算得到的市场数据
        slug: 市场 slug
    
    Returns:
        验证是否通过
    """
    gamma_data = fetch_gamma_market(slug)
    
    if not gamma_data:
        print("Warning: Could not fetch Gamma API data for verification", file=sys.stderr)
        return False
    
    # 提取 Gamma 数据中的 tokenIds
    gamma_token_ids = []
    if 'tokens' in gamma_data:
        for token in gamma_data['tokens']:
            if 'tokenId' in token:
                gamma_token_ids.append(str(token['tokenId']))
    
    # 或者从 clobTokenIds 获取
    if 'clobTokenIds' in gamma_data:
        gamma_token_ids = [str(tid) for tid in gamma_data['clobTokenIds']]
    
    if not gamma_token_ids:
        print("Warning: No tokenIds found in Gamma API response", file=sys.stderr)
        return False
    
    # 验证 tokenIds 是否匹配
    calculated_ids = {market_data['yesTokenId'], market_data['noTokenId']}
    gamma_ids = set(gamma_token_ids)
    
    if calculated_ids == gamma_ids:
        print("✓ TokenIds match Gamma API data", file=sys.stderr)
        return True
    else:
        print(f"✗ TokenIds do not match!", file=sys.stderr)
        print(f"  Calculated: {calculated_ids}", file=sys.stderr)
        print(f"  Gamma API:  {gamma_ids}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket 市场参数解码器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从 conditionId 计算
  python market_decoder.py --condition-id 0xabc123... --oracle 0xOracle... --question-id 0xdef456...
  
  # 从交易日志解码
  python market_decoder.py --tx-hash 0x123... --log-index 5
  
  # 从 Gamma API 获取并计算（市场 slug）
  python market_decoder.py --gamma-slug fed-rate-jan2024 --verify
  
  # 从 Gamma API 获取并计算（事件 slug，使用第一个市场）
  python market_decoder.py --gamma-slug fed-decision-in-january
  
  # 从 Gamma API 获取并计算（事件 slug，指定市场索引）
  python market_decoder.py --gamma-slug fed-decision-in-january --market-index 2
        """
    )
    
    # 输入方式（互斥）
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--condition-id",
        help="条件ID（需要同时提供 --oracle 和 --question-id）"
    )
    input_group.add_argument(
        "--tx-hash",
        help="交易哈希（需要同时提供 --log-index）"
    )
    input_group.add_argument(
        "--gamma-slug",
        help="Gamma API 市场 slug 或事件 slug（如果是事件 slug，使用 --market-index 选择市场）"
    )
    
    # 其他参数
    parser.add_argument(
        "--oracle",
        help="预言机地址（与 --condition-id 一起使用）"
    )
    parser.add_argument(
        "--question-id",
        help="问题ID（与 --condition-id 一起使用）"
    )
    parser.add_argument(
        "--log-index",
        type=int,
        help="日志索引（与 --tx-hash 一起使用）"
    )
    parser.add_argument(
        "--rpc-url",
        default=POLYGON_RPC_URL,
        help="Polygon RPC URL（默认从 POLYMARKET_RPC_URL / NODE_URL 读取）"
    )
    parser.add_argument(
        "--collateral-token",
        default=USDC_E_ADDRESS,
        help=f"抵押品代币地址（默认: {USDC_E_ADDRESS}）"
    )
    parser.add_argument(
        "--ctf-address",
        default=CONDITIONAL_TOKENS_ADDRESS,
        help=f"ConditionalTokens 合约地址（默认: {CONDITIONAL_TOKENS_ADDRESS}）"
    )
    parser.add_argument(
        "--use-onchain",
        action="store_true",
        help="调用链上合约获取精确的 collectionId（需要 RPC 连接）"
    )
    parser.add_argument(
        "--market-index",
        type=int,
        help="如果 --gamma-slug 是事件 slug，指定要使用的市场索引（默认: 0，即第一个市场）"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="使用 Gamma API 验证计算结果（仅与 --gamma-slug 一起使用）"
    )
    parser.add_argument(
        "--output",
        "-o",
        help="输出JSON文件路径（如果不指定，输出到stdout）"
    )
    
    args = parser.parse_args()
    
    try:
        market_data = None
        
        # 准备 Web3 实例（如果需要调用链上合约）
        w3 = None
        if args.use_onchain:
            w3 = Web3(Web3.HTTPProvider(args.rpc_url))
            if not w3.is_connected():
                raise ConnectionError(f"Failed to connect to RPC: {args.rpc_url}")
        
        # 方式1: 从 conditionId 计算
        if args.condition_id:
            if not args.oracle or not args.question_id:
                parser.error("--condition-id requires --oracle and --question-id")
            
            # 验证 conditionId 格式
            if not args.condition_id.startswith("0x"):
                args.condition_id = "0x" + args.condition_id
            
            if len(args.condition_id) != 66:
                raise ValueError(f"Invalid conditionId format: {args.condition_id}")
            
            market_data = calculate_market_tokens(
                args.condition_id,
                args.oracle,
                args.question_id,
                collateral_token=args.collateral_token,
                w3=w3,
                ctf_address=args.ctf_address if args.use_onchain else None
            )
        
        # 方式2: 从交易日志解码
        elif args.tx_hash:
            if args.log_index is None:
                parser.error("--tx-hash requires --log-index")
            
            if not args.tx_hash.startswith("0x"):
                args.tx_hash = "0x" + args.tx_hash
            
            print(f"Decoding from transaction: {args.tx_hash}, log index: {args.log_index}", file=sys.stderr)
            # 如果需要调用链上合约，创建 Web3 实例
            if args.use_onchain and not w3:
                w3 = Web3(Web3.HTTPProvider(args.rpc_url))
                if not w3.is_connected():
                    raise ConnectionError(f"Failed to connect to RPC: {args.rpc_url}")
            market_data = decode_market_from_log(
                args.tx_hash,
                args.log_index,
                args.rpc_url,
                args.ctf_address if args.use_onchain else None
            )
        
        # 方式3: 从 Gamma API 获取
        elif args.gamma_slug:
            print(f"Fetching market data from Gamma API: {args.gamma_slug}", file=sys.stderr)
            gamma_data = fetch_gamma_market(args.gamma_slug, args.market_index)
            
            if not gamma_data:
                raise ValueError(f"Failed to fetch market data for slug: {args.gamma_slug}")
            
            # 提取必要信息
            condition_id = gamma_data.get('conditionId')
            # Gamma API 使用 questionID (大写)，而不是 questionId
            question_id = gamma_data.get('questionID') or gamma_data.get('questionId')
            # Oracle 可能在 resolvedBy 字段中，或者需要从其他地方获取
            oracle = gamma_data.get('oracle', {}).get('address') if isinstance(gamma_data.get('oracle'), dict) else gamma_data.get('oracle')
            # 如果 oracle 不存在，尝试使用 resolvedBy（可能是 oracle 地址，但不一定）
            if not oracle:
                oracle = gamma_data.get('resolvedBy')
            
            if not condition_id:
                raise ValueError(f"Missing conditionId in Gamma API response. Available keys: {list(gamma_data.keys())[:20]}")
            if not question_id:
                raise ValueError(f"Missing questionID/questionId in Gamma API response. Available keys: {list(gamma_data.keys())[:20]}")
            if not oracle:
                # 如果仍然没有 oracle，尝试从链上查询 ConditionPreparation 事件
                print("Warning: Oracle address not found in Gamma API response. Attempting to query from chain...", file=sys.stderr)
                # 确保有 Web3 实例
                if not w3:
                    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
                    if not w3.is_connected():
                        raise ConnectionError(f"Failed to connect to RPC: {args.rpc_url}")
                
                oracle = get_oracle_from_condition_id(
                    condition_id,
                    args.rpc_url,
                    args.ctf_address  # 使用指定的 CTF 地址，如果没有则使用默认值
                )
                
                if not oracle:
                    raise ValueError(
                        "Oracle address not found in Gamma API response and could not query from chain. "
                        "Please use --condition-id with --oracle and --question-id instead, "
                        "or use --tx-hash with --log-index to decode from transaction logs."
                    )
                print(f"Successfully queried oracle from chain: {oracle}", file=sys.stderr)
            
            # 如果需要调用链上合约，创建 Web3 实例
            if args.use_onchain and not w3:
                w3 = Web3(Web3.HTTPProvider(args.rpc_url))
                if not w3.is_connected():
                    raise ConnectionError(f"Failed to connect to RPC: {args.rpc_url}")
            
            market_data = calculate_market_tokens(
                condition_id,
                oracle,
                question_id,
                collateral_token=args.collateral_token,
                w3=w3,
                ctf_address=args.ctf_address if args.use_onchain else None
            )
            
            # 验证
            if args.verify:
                verify_with_gamma(market_data, args.gamma_slug)
        
        if not market_data:
            raise ValueError("Failed to decode market data")
        
        # 格式化输出（移除中间计算的 collectionId）
        output_data = {
            "conditionId": market_data['conditionId'],
            "questionId": market_data['questionId'],
            "oracle": market_data['oracle'],
            "collateralToken": market_data['collateralToken'],
            "yesTokenId": market_data['yesTokenId'],
            "noTokenId": market_data['noTokenId']
        }
        
        # 输出 JSON
        output_json = json.dumps(output_data, indent=2, ensure_ascii=False)
        
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
