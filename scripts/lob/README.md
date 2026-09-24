# Polymarket LOB Service

这个目录提供一套和现有 `markets` / `trades` 对齐的 LOB 流式写库实现，目标是可长期运行、可恢复、可维护。

## 文件

- `lob_service.py`: 核心实现，包含 schema、market/token 映射、消息标准化、写库、reconcile、live runner CLI。
- `lob_live_writer.py`: 生产入口，直接调用 `lob_service.py run-live`。
- `test_lob_service.py`: 纯本地 SQLite 单元测试。
- `guide.md`: 原始需求说明。

## 设计要点

- 任何 LOB 行在写入前都必须先解析出 `market_id`，否则进入 `lob_dead_letters`，不会生成脏数据。
- `market_tokens` 作为 `markets` 的派生维表，统一承接 YES/NO token 到 `market_id` 的映射，LOB 和 trades 后续都可以共享。
- `market_subscriptions` 记录当前订阅意图、活跃状态、最后消息时间、错误计数，支持自动增订阅、停订阅与 stale 检查。
- `lob_snapshots` 存归一化后的 LOB/BBO/price_change/last_trade/tick_size 事件，`lob_levels` 只保存 `book` 深度档位。
- 未知 token 会先触发 `market_discovery.fetch_and_upsert_markets_for_token_ids(...)`，补齐后再写库；补齐失败则进死信。

## 命令

初始化 schema：

```bash
python scripts/lob/lob_service.py init-schema --backend sqlite --sqlite-path /tmp/polymarket_lob.sqlite --sync-subscriptions
```

从 `markets` 刷新订阅表：

```bash
python scripts/lob/lob_service.py sync-markets --backend postgres
```

执行一次一致性检查：

```bash
python scripts/lob/lob_service.py reconcile --backend sqlite --sqlite-path /tmp/polymarket_lob.sqlite --stale-after-seconds 600
```

运行生产 LOB 写入：

```bash
python scripts/lob/lob_live_writer.py run-live \
  --backend postgres \
  --stream-name polymarket_lob_prod \
  --sync-interval-seconds 60 \
  --heartbeat-seconds 10 \
  --best-bid-ask-throttle-ms 1000 \
  --price-change-throttle-ms 250 \
  --bootstrap-market-limit 200 \
  --verbose
```

做短时 smoke test：

```bash
python scripts/lob/lob_live_writer.py run-live \
  --backend sqlite \
  --sqlite-path /tmp/polymarket_lob_smoke.sqlite \
  --bootstrap-market-limit 5 \
  --run-seconds 15 \
  --verbose
```

运行测试：

```bash
python -m unittest discover -s scripts/lob -p 'test_*.py'
```
