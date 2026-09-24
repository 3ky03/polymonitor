请为我设计并实现一个 **Polymarket LOB（Limit Order Book）长期流式监控与数据库落地系统**，要求它能和我现有的 **Market Discovery + Trades Indexer** 体系严格对齐，形成一套可长期运行、可恢复、可维护的数据管道。

---

# 背景与已有系统

我已经有两套基础能力：

## 1. Market Discovery Service
负责周期性从 Polymarket Gamma API 发现新市场，并写入 `markets` 表。  
已知会存储：
- market 基本信息
- slug / conditionId / questionId / oracleAddress
- outcomeSlotCount
- collateralToken
- yes/no tokenId（clobTokenIds）
- market 状态、截止时间、created_at
- event 相关信息
- enable_neg_risk 等

## 2. Trades Indexer
负责通过链上 `OrderFilled` 日志扫描历史成交，并写入 `trades` 表。  
已知会做到：
- 解析 maker / taker / tokenId / price / size / tx_hash / log_index
- 通过 tokenId 映射到 `market_id`
- 区分 YES / NO
- 存储 timestamp / block_number
- 动态补市场（遇到未知 tokenId 时触发 Market Discovery）

---

# 现在的新需求

我要新增一个 **LOB Streaming Service**，长期流式监控 Polymarket 的订单簿数据（LOB / order book / best bid ask / price change），并持续写入数据库。

关键要求：

1. **LOB 必须和 market 对齐**
   - 任何一条 LOB 数据都必须能映射到一个明确的 `market_id`
   - 不能只存 asset_id 而不建立 market 关联
   - YES / NO token 的 LOB 要能区分
   - 最好支持 market-level 与 token-level 两层映射

2. **LOB 和 market 要同步维护**
   - 新 market 被发现后，应自动纳入 LOB 订阅
   - market 失效、关闭、resolved 后，应自动停止或降级订阅
   - 如果流中出现未知 asset_id / token_id，应自动触发市场补录逻辑
   - 要保证 market 元数据和 LOB 数据生命周期一致

3. **适合长期运行**
   - 支持 WebSocket 长连接
   - 支持断线重连
   - 支持订阅恢复
   - 支持心跳 / 健康检查
   - 支持幂等写库
   - 支持异常恢复和 backfill 策略

---

# 你需要输出的内容

请给我一份完整的系统设计与实现方案，至少包含以下部分：

## A. 总体架构
说明应该拆成哪些服务/模块，例如：
- market discovery worker
- market subscription manager
- websocket LOB listener
- message normalizer
- database writer
- reconciliation worker
- health monitor

并说明这些模块之间如何协作。

---

## B. LOB 订阅对象设计
请明确说明：

1. Polymarket 的 LOB 流应该按什么粒度订阅：
   - market?
   - asset/token?
   - yes/no token?
   - event?

2. 如何从现有 `markets` 表派生订阅列表：
   - 每个 market 订阅 YES/NO 两个 token?
   - 是否只订阅 active market?
   - neg-risk / multi-market event 是否需要特殊处理?

3. 如何处理新增 market：
   - market discovery 写库后如何通知 LOB service 增量订阅?
   - 轮询数据库还是事件驱动?

---

## C. 数据库表设计
请设计一套适合长期存储 LOB 的数据库 schema，至少包含：

### 1. `lob_snapshots`
用于存订单簿快照或归一化后的时点状态  
建议字段示例：
- id
- market_id
- asset_id / token_id
- outcome（YES / NO）
- best_bid
- best_ask
- midpoint
- spread
- last_trade_price（如流里能拿到）
- timestamp
- source
- received_at
- sequence_id / update_id（如果流里有）
- raw_payload（可选）

### 2. `lob_levels`
如果需要保存深度盘口，请设计逐档表  
例如：
- snapshot_id
- side（bid/ask）
- price
- size
- level_index

### 3. `market_subscriptions`
用于维护当前哪些 market/token 正在订阅  
字段示例：
- market_id
- asset_id
- outcome
- subscribe_status
- active
- last_subscribed_at
- last_message_at
- error_count

### 4. `lob_stream_state`
用于断点恢复、流状态维护  
字段示例：
- stream_name
- cursor / seq / last_seen_time
- connection_status
- reconnect_count
- updated_at

请说明每张表的用途、主键、唯一键、索引建议，以及哪些字段必须建立联合索引。

---

## D. market 与 LOB 的映射逻辑
这是重点。请你详细说明：

1. 如何通过 `asset_id / token_id` 映射回 `market_id`
2. 如何区分 YES / NO
3. 如何处理 market 尚未发现、但流里先来了 token 的情况
4. 如何避免“LOB 数据写进去了，但没有 market_id”的脏数据
5. 是否需要一张单独的 `market_tokens` 映射表来提升性能

请给出推荐的数据模型。

---

## E. 流式处理逻辑
请给出从 websocket 收到消息到写库的完整处理链路：

1. 建立连接
2. 订阅目标 market / asset
3. 接收 LOB 事件
4. 解析消息类型（book / price_change / best_bid_ask / last_trade_price 等）
5. 标准化成统一内部模型
6. 关联 market_id
7. 落库
8. 更新 subscription / stream_state
9. 失败重试 / 死信处理

请明确说明：
- 哪些消息适合存成 snapshot
- 哪些消息只更新 best bid/ask
- 哪些消息适合直接丢弃
- 如何控制写库频率，避免每条微小变动都爆炸式写入

---

## F. 长期运行策略
请重点设计长期运行能力，包括：

1. 断线重连
2. 重连后重新订阅
3. 如何避免重复写入
4. 如何检测某个 market 长时间无消息
5. 如何处理 websocket 消息乱序
6. 如何处理短暂网络抖动
7. 如何做日志与告警
8. 如何做每日巡检 / reconciliation

---

## G. LOB 与 market 的同步维护机制
请告诉我如何保证二者长期一致：

1. market 新增时，LOB 订阅如何自动扩展
2. market inactive / resolved 时，LOB 如何停订或归档
3. market 元数据变更时，LOB 表是否要联动更新
4. 如果发现数据库里有 active market 但未订阅，如何自动修复
5. 如果发现正在订阅的 asset 在 `markets` 表中不存在，如何自动修复

请给出一套“周期性一致性检查任务”的方案。

---

## H. 与 trades 的协同
请说明如何让 LOB 与现有 `trades` 索引器协同，而不是冲突：

1. 如何共享 market/token 映射
2. 是否共用 `market_tokens` 维表
3. trade 与 LOB 的 timestamp / block / event time 有什么区别
4. 如何用 LOB 辅助解释 trades
5. 后续如果要重建 executable benchmark / BBO series，LOB 表该如何设计以支持

---

## I. 技术选型建议
请结合“长期流式监控 + 写数据库”的需求，给出技术栈建议，包括但不限于：
- TypeScript 还是 Python
- WebSocket 客户端怎么组织
- ORM / query builder 如何选
- PostgreSQL / TimescaleDB / ClickHouse 这类存储怎么选
- 是否需要 Redis 做缓存 / 去重 / 订阅协调

要求你明确说明：
- 如果偏生产长期运行，推荐什么
- 如果偏研究原型，推荐什么

---

## J. 最终交付格式
请按以下格式输出：

1. 系统总体设计
2. 表结构设计
3. 数据流转流程图（文字版即可）
4. market-LOB 对齐策略
5. 异常与恢复策略
6. 推荐实现步骤（按优先级分阶段）
7. 一个最小可运行版本（MVP）的实现建议
8. 后续扩展建议（例如 BBO、full depth、OHLCV、回测支持）

---

# 额外要求

- 回答要以“真正可长期运行的工程系统”为目标，不要只给玩具脚本
- 要特别强调 **market 与 LOB 对齐** 这件事
- 要考虑新增市场、市场失效、未知 token、重复订阅、断线重连等真实问题
- 设计要能兼容我现有的 `markets` 和 `trades` 体系
- 尽量给出务实的数据库字段、索引和维护策略
- 不要泛泛而谈，请尽量具体