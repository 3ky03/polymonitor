#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 索引器配置

RPC URL 优先从环境变量读取，支持 .env 或系统变量
"""

import os
from pathlib import Path

from data_sources import POLYGON_RPC_URL

# 尝试加载 Polymonitor 自己的 .env。不要再从相邻的 chainStackNode
# 项目继承托管 RPC 凭据；自建 Polygon 节点不可用时应明确失败。
_base = Path(__file__).resolve().parent
_env_paths = [
    _base,
    _base.parent,
]
for _p in _env_paths:
    if not _p or not _p.exists():
        continue
    _env = _p / ".env"
    if _env.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_env)
        except ImportError:
            pass
        break

# Polygon RPC 必须由 POLYMARKET_RPC_URL 明确指定。生产环境使用本机 SSH
# 转发地址，远端 Bor RPC 只监听 loopback。
DEFAULT_RPC_URL = POLYGON_RPC_URL

RPC_ENV_KEY = "POLYMARKET_RPC_URL"


def get_rpc_url() -> str:
    """
    获取 Polygon RPC URL
    只接受 POLYMARKET_RPC_URL；不再回退到旧的 NODE_URL/Chainstack。
    """
    url = os.environ.get(RPC_ENV_KEY)
    if url:
        # 处理 .env 中可能带引号的值
        return url.strip().strip('"').strip("'")
    if DEFAULT_RPC_URL:
        return DEFAULT_RPC_URL
    raise RuntimeError(
        "POLYMARKET_RPC_URL is required; configure the self-hosted Polygon RPC tunnel"
    )
