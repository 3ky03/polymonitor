#!/usr/bin/env python3
"""Generate Simplified Chinese mirrors for the static docs pages.

The docs are committed as static HTML. This script keeps English as the
canonical/default language and creates /docs/zh/... mirrors with translated UI
chrome, sidebar, TOC, and page content.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "webpage" / "public" / "docs"
CSS_VERSION = "20260610-i18n-zh"

SKIP_TAGS = {"script", "style", "svg", "path", "circle", "pre", "code", "kbd"}

TITLE_ZH = {
    "Polymarket Concepts": "Polymarket 概念",
    "Data Coverage": "数据覆盖",
    "Deployment": "部署",
    "Introduction": "介绍",
    "Errors": "错误状态",
    "Features & Interface": "功能与界面",
    "Market": "Market 概念",
    "Oracle": "Oracle 概念",
    "Oracle Watch": "Oracle 观察",
    "Orderfilled": "OrderFilled 概念",
    "Panel Modules": "Panel 模块",
    "CPI Panels": "CPI Panels",
    "Flow & Trading Panels": "流动与交易 Panels",
    "Intelligence & News Panels": "情报与新闻 Panels",
    "Macro, Weather & Sports Panels": "宏观、天气与体育 Panels",
    "Market & Lifecycle Panels": "市场与生命周期 Panels",
    "Platform Overview": "平台概览",
    "Polymarket Agent": "Polymarket Agent",
    "Realtime Coverage": "实时覆盖",
    "Research Links": "研究链接",
    "Runtime API": "Runtime API",
    "Signal Intelligence": "信号情报",
    "What polyData tracks": "polyData 追踪什么",
}

TEXT_ZH = {
    # Shared chrome.
    "Search...": "搜索...",
    "Ask Assistant": "问助手",
    "Blog": "博客",
    "Dashboard": "仪表盘",
    "Pro": "Pro",
    "GitHub": "GitHub",
    "Get Early Access": "抢先体验",
    "Documentation": "文档",
    "API Reference": "API 参考",
    "Changelog": "更新日志",
    "On this page": "本页目录",
    "Getting Started": "开始使用",
    "Introduction": "介绍",
    "What polyData tracks": "polyData 追踪什么",
    "Data Coverage": "数据覆盖",
    "Polymarket Concepts": "Polymarket 概念",
    "Panels": "Panels",
    "Market & Lifecycle": "市场与生命周期",
    "Flow & Trading": "流动与交易",
    "Intelligence & News": "情报与新闻",
    "Polymarket Agent": "Polymarket Agent",
    "CPI": "CPI",
    "Macro, Weather & Sports": "宏观、天气与体育",
    "Platform & Features": "平台与功能",
    "Platform Overview": "平台概览",
    "Features & Interface": "功能与界面",
    "Panel Modules": "Panel 模块",
    "Deployment": "部署",
    "Intelligence & Analysis": "情报与分析",
    "Signal Intelligence": "信号情报",
    "Oracle Watch": "Oracle 观察",
    "Research Links": "研究链接",
    "Usage": "使用",
    "Realtime Coverage": "实时覆盖",
    "Runtime API": "Runtime API",
    "Errors": "错误状态",
    # Common headings/table terms.
    "Lifecycle Overview": "生命周期概览",
    "Market": "Market",
    "OrderFilled": "OrderFilled",
    "Oracle": "Oracle",
    "Coverage window": "覆盖窗口",
    "Backfill path": "回填路径",
    "Live path": "实时路径",
    "Static serving": "静态服务",
    "What polyData is": "polyData 是什么",
    "Next": "下一步",
    "Degraded states": "降级状态",
    "Market lifecycle model": "市场生命周期模型",
    "Resolution stage": "结算阶段",
    "Settlement risk": "结算风险",
    "Panel registry": "Panel 注册表",
    "Panel coverage": "Panel 覆盖范围",
    "What CPI Coverage Includes": "CPI 覆盖包含什么",
    "Data Sources": "数据源",
    "CPI Release Command": "CPI 发布指挥面板",
    "CPI Components": "CPI 分项",
    "CPI Driver Panels": "CPI 驱动 Panels",
    "Actuals, Forecasts, Nowcasts, and Caches": "实际值、预测、Nowcast 与缓存",
    "Runtime Fields": "Runtime 字段",
    "API Panel IDs": "API Panel ID",
    "OrderFilled Flow": "OrderFilled 流动",
    "Signal Panels": "信号 Panels",
    "Crypto and Finance Flow": "Crypto 与金融流动",
    "Oracle and AI Analysis": "Oracle 与 AI 分析",
    "Technology and Finance Intelligence": "科技与金融情报",
    "Policy, Risk, and Event Feeds": "政策、风险与事件 feeds",
    "Macro and Inflation Panels": "宏观与通胀 Panels",
    "Market and Commodity Context": "市场与大宗商品上下文",
    "Weather Panels": "天气 Panels",
    "Sports Panels": "体育 Panels",
    "Market Discovery": "市场发现",
    "Focused Market Workspace": "聚焦市场工作区",
    "Lifecycle State": "生命周期状态",
    "Platform Variants": "平台版本",
    "polyMonitor": "polyMonitor",
    "Quant Workspace": "Quant 工作区",
    "World Cup Workspace": "世界杯工作区",
    "Shared Stack": "共享技术栈",
    "Abstract": "摘要",
    "Problem Formulation": "问题定义",
    "System Overview": "系统概览",
    "Architecture": "架构",
    "Agent Prompts": "Agent Prompts",
    "Inference Procedure": "推理流程",
    "Panel Lenses": "Panel 视角",
    "Input Representation": "输入表示",
    "Output Schema": "输出 Schema",
    "Evaluation Protocol": "评估协议",
    "Ablations": "消融实验",
    "Limitations": "局限性",
    "Related Work": "相关工作",
    "Runtime APIs": "Runtime APIs",
    "Collection paths": "采集路径",
    "Cache layers": "缓存层",
    "Sync loop": "同步循环",
    "Project links": "项目链接",
    "Runtime routing": "Runtime 路由",
    "Signal layer": "信号层",
    "Market identity": "市场身份",
    "Live market state": "实时市场状态",
    "Chain and oracle context": "链上与 Oracle 上下文",
    "External context": "外部上下文",
    "Variant": "版本",
    "URL": "URL",
    "Focus": "重点",
    "Capability": "能力",
    "Description": "说明",
    "Market Lifecycle": "市场生命周期",
    "Oracle Watch": "Oracle 观察",
    "Context Panels": "上下文 Panels",
    "Price Sources": "价格来源",
    "Backtest Runs": "回测运行",
    "Research Output": "研究输出",
    "Build Monitoring": "构建监控",
    "Schedule & Match Detail": "赛程与比赛详情",
    "Host-city Map": "主办城市地图",
    "Weather & Venue Context": "天气与场馆上下文",
    "Market Intelligence": "市场情报",
    "Layer": "层",
    "What it contains": "包含内容",
    "Where it appears": "出现位置",
    "Source": "来源",
    "Used for": "用途",
    "Data type": "数据类型",
    "Field": "字段",
    "Meaning": "含义",
    "Important note": "重要说明",
    "Component area": "分项区域",
    "Examples": "示例",
    "How to read it": "如何解读",
    "Panel": "Panel",
    "Panel id": "Panel ID",
    "What it adds": "补充内容",
    "Example": "示例",
    "What it shows": "显示内容",
    "How to use it": "如何使用",
    "Node": "节点",
    "Type": "类型",
    "Responsibility": "职责",
    "Lens": "视角",
    "What it should emphasize": "应强调什么",
    "Input surface": "输入表面",
    "Used by": "使用者",
    "Endpoint": "Endpoint",
    "Returns": "返回内容",
    "Ablation": "消融项",
    "Question": "问题",
    # Redirect pages.
    "Open Market concepts": "打开 Market 概念",
    "Open Oracle concepts": "打开 Oracle 概念",
    "Open Orderfilled concepts": "打开 OrderFilled 概念",
    # Main page copy.
    "Polymarket market lifecycle in polyData Monitor: market identity, OrderFilled trading activity, and oracle resolution state.": "polyData Monitor 中的 Polymarket 市场生命周期：市场身份、OrderFilled 交易活动与 oracle 结算状态。",
    "Polymarket has five concepts that are easy to confuse: market, event, series, slug, and token. A token is the lowest-level settleable asset. A market is the tradable question. An event organizes one or more related markets. A series is a broader collection that can group events across seasons, recurring themes, elections, tournaments, or long-running topics. A slug is a human-readable lookup key used in URLs and APIs.": "Polymarket 中有五个容易混淆的概念：market、event、series、slug 和 token。token 是最低层的可结算资产；market 是可交易的问题；event 用来组织一个或多个相关 market；series 是更大的集合，可跨赛季、重复主题、选举、赛事或长期专题组织 event；slug 是用于 URL 和 API 的人类可读查询键。",
    "A market is the smallest tradable unit. In the common binary case, it asks one concrete Yes/No question and has two outcome tokens. An event is the page-level container users often see in the Polymarket frontend. A slug is not a chain id and it is not the asset a wallet holds. The token is the actual result asset.": "market 是最小可交易单位。在常见二元市场中，它对应一个具体的 Yes/No 问题，并拥有两个 outcome token。event 是用户在 Polymarket 前端通常看到的页面级容器。slug 不是链上 ID，也不是钱包真正持有的资产；token 才是真正的结果资产。",
    "Polymarket price history is not traditional OHLCV candle data. It is midpoint-oriented price history. Midpoint is the average of the current best bid and best ask. The frontend generally displays midpoint and falls back to last traded price when the spread is wider than $0.10. Live order book data is a separate layer, and Polymarket does not provide a complete official historical LOB archive.": "Polymarket 的历史价格不是传统 OHLCV K 线，而是以 midpoint 为核心的价格历史。midpoint 是当前最优买价和最优卖价的平均值。前端通常显示 midpoint，当 spread 大于 0.10 美元时会回退显示最近成交价。实时订单簿是另一层数据，Polymarket 并不提供完整官方历史 LOB 档案。",
    "A trade is the execution-result view: it says which market traded, which outcome was involved, what side the taker took, what price was matched, what total size traded, and which maker orders were consumed.": "trade 是成交结果视角：它描述哪个市场发生了成交、涉及哪个 outcome、taker 的方向、撮合价格、总成交数量，以及吃掉了哪些 maker 订单。",
    "OrderFilled is the on-chain fill-event view. When execution is settled through the Exchange contract, the contract emits fill-level events with orderHash, maker, taker, makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, and fee. In short: trade is the execution receipt; OrderFilled is the fill-level ledger.": "OrderFilled 是链上 fill 事件视角。当成交通过 Exchange 合约结算时，合约会发出 fill 级事件，包含 orderHash、maker、taker、makerAssetId、takerAssetId、makerAmountFilled、takerAmountFilled 和 fee。简言之：trade 是成交回执；OrderFilled 是 fill 级账本。",
    "Maker and taker are roles in a specific execution, not permanent user categories. Order, trade, fill, and transaction are also separate layers: an order is the instruction, a trade is the matching result, OrderFilled is the chain-level fill event, and a transaction is the blockchain settlement record.": "maker 和 taker 是某次成交中的角色，不是永久用户类别。order、trade、fill 和 transaction 也是不同层级：order 是指令，trade 是撮合结果，OrderFilled 是链上 fill 事件，transaction 是区块链结算记录。",
    "Oracle data explains how a market leaves the trading phase and becomes a settled outcome. Price alone does not prove settlement. The oracle layer records what answer was requested, what was proposed, whether the proposal was disputed, and what finally settled.": "Oracle 数据解释市场如何离开交易阶段并形成最终结算结果。价格本身不能证明结算。oracle 层记录请求了什么答案、提出了什么结果、是否发生争议，以及最终如何结算。",
    "Many Polymarket markets resolve through UMA-related adapter and oracle contracts. The lifecycle can include question initialization, request, proposal, dispute, and settlement. polyData stores transaction hashes, log indexes, block numbers, event times, proposed prices, settled prices, participants, and matching metadata for these events.": "许多 Polymarket 市场通过 UMA 相关 adapter 和 oracle 合约结算。生命周期可能包含问题初始化、请求、提议、争议和结算。polyData 会保存这些事件的交易哈希、日志索引、区块号、事件时间、提议价格、结算价格、参与者以及匹配元数据。",
    "The hard part is identity matching. Some oracle events can be matched directly through question id or condition id. Others require adapter mappings, ancillary data decoding, title/date matching, or neg-risk request mappings.": "难点在身份匹配。有些 oracle 事件可以通过 question id 或 condition id 直接匹配；另一些则需要 adapter 映射、ancillary data 解码、标题/日期匹配，或 neg-risk request 映射。",
    "As of June 6, 2026, the local PostgreSQL market catalog contains 1,280,986 market rows. The earliest indexed market creation time is October 2, 2020, and the newest market rows are collected through June 6, 2026.": "截至 2026 年 6 月 6 日，本地 PostgreSQL 市场目录包含 1,280,986 条 market 记录。最早索引到的市场创建时间是 2020 年 10 月 2 日，最新市场记录采集到 2026 年 6 月 6 日。",
    "The UMA oracle index contains 2,778,072 oracle events from July 11, 2022 through May 3, 2026. The ClickHouse OrderFilled store contains more than 1.44 billion trade rows and more than 230 million non-trade cashflow rows, covering Polygon blocks from 5,243,433 through 88,017,065, which maps to October 2, 2020 through June 6, 2026.": "UMA oracle 索引包含 2,778,072 条 oracle 事件，时间范围从 2022 年 7 月 11 日到 2026 年 5 月 3 日。ClickHouse OrderFilled 存储包含超过 14.4 亿条 trade 行和超过 2.3 亿条非交易 cashflow 行，覆盖 Polygon 区块 5,243,433 到 88,017,065，对应 2020 年 10 月 2 日到 2026 年 6 月 6 日。",
    "The backfill path starts from Polymarket Gamma active and closed events, hydrates missing fields from CLOB by slug or condition id, and supplements missed markets from Polygon exchange registry events.": "回填路径从 Polymarket Gamma 的 active/closed events 开始，通过 slug 或 condition id 从 CLOB 补齐缺失字段，并从 Polygon exchange registry 事件中补充遗漏市场。",
    "The live path keeps moving forward with checkpointed market discovery, overlapping block scans, and a shared safe high-water block for market, oracle, and trade sync.": "实时路径依靠带 checkpoint 的市场发现、重叠区块扫描，以及 market/oracle/trade 同步共享的安全高水位区块持续向前推进。",
    "For production-style hosting, build the frontend locally or on the serving host, publish webpage/dist into the static web root, and keep API process management in systemd.": "生产式部署时，在本地或服务主机上构建前端，将 webpage/dist 发布到静态 Web 根目录，并使用 systemd 管理 API 进程。",
    "polyData Monitor is an open-source Polymarket intelligence system. It turns markets, order books, on-chain trades, UMA oracle activity, outside news, macro data, weather data, sports data, and AI summaries into one operator-facing dashboard for understanding what is moving, why it is moving, and where liquidity or resolution risk is building.": "polyData Monitor 是一个开源 Polymarket 情报系统。它把市场、订单簿、链上交易、UMA oracle 活动、外部新闻、宏观数据、天气数据、体育数据和 AI 摘要整合到一个面向操作员的仪表盘中，用来理解什么正在移动、为什么移动，以及流动性或结算风险在哪里累积。",
    "The project is not just a frontend over the public Polymarket website. It maintains its own market catalog, token catalog, chain index, runtime snapshots, and panel registry so analysts can inspect a market from listing through trading, closure, oracle review, settlement, and archive.": "这个项目不只是公共 Polymarket 网站外面套的一层前端。它维护自己的 market catalog、token catalog、链上索引、runtime snapshots 和 panel registry，使分析员可以从上市、交易、关闭、oracle review、结算到归档完整检查一个市场。",
    "Start with What polyData tracks , then read Polymarket Concepts for the market, OrderFilled, and oracle data model.": "建议先阅读《polyData 追踪什么》，再阅读《Polymarket 概念》了解 market、OrderFilled 和 oracle 数据模型。",
    "Panels should show cached, stale, empty, or degraded states clearly. Operators can verify service health through the public deployment templates and the runtime health endpoints.": "Panels 应清楚展示 cached、stale、empty 或 degraded 状态。操作员可以通过公开部署模板和 runtime health endpoints 验证服务健康状态。",
    "polyData treats a Polymarket market as a lifecycle, not as a static row. The lifecycle starts when a market is discovered from Gamma, CLOB, or on-chain TokenRegistered events. It becomes tradeable when condition id and CLOB token ids are available, then active when prices, BBO, volume, or OrderFilled flow appear.": "polyData 将 Polymarket market 视为一个生命周期对象，而不是静态表行。生命周期从 Gamma、CLOB 或链上 TokenRegistered 事件发现市场开始；当 condition id 和 CLOB token id 可用时变为可交易；当价格、BBO、成交量或 OrderFilled flow 出现时进入 active 状态。",
    "The market moves into closing when the end date passes or trading flags close, then into oracle review when UMA request, proposal, dispute, or settlement events are matched. It becomes resolved when final status and settlement outcome are known, then archived while still remaining queryable.": "当 end date 到达或交易标记关闭时，市场进入 closing；当匹配到 UMA request、proposal、dispute 或 settlement 事件时进入 oracle review；当最终状态和结算结果已知后变为 resolved，随后进入 archived，但仍可查询。",
    "Oracle panels track proposal, settlement, and resolution context so markets with nearby UMA or settlement risk are easier to spot.": "Oracle panels 追踪 proposal、settlement 和 resolution 上下文，使临近 UMA 或存在结算风险的市场更容易被发现。",
    "The dashboard currently registers 68 panels. They are split into small frontend modules with explicit metadata, fetch behavior, and renderers; runtime panels also have backend registry entries and snapshot routes.": "当前仪表盘注册了 68 个 panels。它们被拆分为小型前端模块，具有明确的 metadata、fetch 行为和 renderer；runtime panels 还拥有后端 registry entry 和 snapshot route。",
    "Core market, chain, oracle, AI, finance, crypto, macro, weather, sports, and time panels give the dashboard separate surfaces for discovery, context, flow, settlement risk, and external catalysts.": "核心 market、chain、oracle、AI、finance、crypto、macro、weather、sports 和 time panels 为仪表盘提供独立视图，用于发现、上下文、流动、结算风险和外部催化因素。",
    "polyMonitor platform variants, capabilities, and specialized monitoring configurations for Polymarket intelligence, quantitative research, and event-specific coverage.": "polyMonitor 的平台版本、能力和专用监控配置，覆盖 Polymarket 情报、量化研究与事件专属监控。",
    "polyMonitor runs three specialized workspaces from a single codebase, each optimized for different monitoring needs.": "polyMonitor 从同一套代码运行三个专用工作区，每个工作区都针对不同监控需求优化。",
    "Market-wide Polymarket intelligence, lifecycle monitoring, chain flow, oracle state, news context, and modular dashboard panels.": "全市场 Polymarket 情报、生命周期监控、链上流动、oracle 状态、新闻上下文和模块化仪表盘 panels。",
    "Research and backtesting workspace for Polymarket price series, frontend price history, block-close prices, strategy runs, equity curves, and trade exports.": "面向 Polymarket 价格序列、前端价格历史、block-close 价格、策略运行、权益曲线和交易导出的研究与回测工作区。",
    "FIFA World Cup 2026 monitoring surface with schedule, match detail, host-city map, weather, news, market links, odds context, and tournament intelligence.": "FIFA 2026 世界杯监控界面，包含赛程、比赛详情、主办城市地图、天气、新闻、市场链接、赔率上下文和赛事情报。",
    "Each workspace keeps the same core data model - market identity, price history, order-book state, OrderFilled flow, oracle resolution, external context, and runtime snapshots - while changing the interface around the task at hand.": "每个工作区保留同一套核心数据模型，包括 market identity、price history、order-book state、OrderFilled flow、oracle resolution、external context 和 runtime snapshots，同时围绕具体任务调整界面。",
    "The default workspace is the general Polymarket monitoring surface. It is organized around market discovery, market workspace inspection, lifecycle state, chain-level OrderFilled flow, oracle state, and cross-domain context. The goal is to make a market inspectable as a living system rather than a single probability number.": "默认工作区是通用 Polymarket 监控界面，围绕市场发现、市场工作区检查、生命周期状态、链上 OrderFilled flow、oracle 状态和跨领域上下文组织。目标是把市场作为一个活系统来检查，而不是只看一个概率数字。",
    "Connects Gamma market identity, slug, condition id, outcome tokens, active status, close state, oracle state, settlement, and archival context.": "连接 Gamma market identity、slug、condition id、outcome tokens、active status、close state、oracle state、settlement 与 archival context。",
    "Surfaces live order book state, sample chain trades, global OrderFilled activity, suspicious flow, whale activity, and liquidity anomalies.": "展示实时订单簿状态、样本链上交易、全局 OrderFilled 活动、可疑流动、鲸鱼活动和流动性异常。",
    "Tracks UMA request, proposal, dispute, settlement, final outcome, transaction hashes, and matched market context.": "追踪 UMA request、proposal、dispute、settlement、final outcome、transaction hashes 和匹配到的市场上下文。",
    "Adds news, research links, macro, crypto, finance, weather, sports, and time-aware context to explain why market probabilities are moving.": "加入新闻、研究链接、宏观、crypto、finance、weather、sports 和时间上下文，用来解释市场概率为什么移动。",
    "The Quant workspace is the research and backtesting variant. It focuses on reproducible price data, strategy simulation, and inspection of generated trades rather than broad dashboard surveillance.": "Quant 工作区是研究和回测版本。它聚焦可复现价格数据、策略模拟和生成交易检查，而不是宽泛的仪表盘巡检。",
    "Compares frontend price history and block-close price series so researchers can detect source drift and choose the right backtest input.": "比较前端价格历史和 block-close 价格序列，帮助研究者发现来源漂移并选择正确回测输入。",
    "Creates strategy runs through /wm-api/quant/backtest-runs and tracks status, parameters, metrics, equity, and trades.": "通过 /wm-api/quant/backtest-runs 创建策略运行，并追踪状态、参数、指标、权益和交易。",
    "Creates strategy runs through": "通过以下接口创建策略运行：",
    "and tracks status, parameters, metrics, equity, and trades.": "并追踪状态、参数、指标、权益和交易。",
    "Shows equity curves, drawdown, performance summaries, trade tables, properties, and exportable CSV/JSON artifacts.": "展示权益曲线、回撤、性能摘要、交易表、属性以及可导出的 CSV/JSON artifacts。",
    "Exposes price-build status so users can tell whether the quant dataset is fresh, partial, or still catching up.": "暴露 price-build 状态，让用户判断 quant 数据集是新鲜、部分完成，还是仍在追赶。",
    "The World Cup workspace is an event-specific variant for FIFA World Cup 2026. It treats the tournament as a monitoring surface where schedule, venues, teams, weather, news, odds, and Polymarket markets all affect interpretation.": "世界杯工作区是面向 FIFA 2026 世界杯的事件专属版本。它将赛事作为一个监控界面，赛程、场馆、球队、天气、新闻、赔率和 Polymarket markets 都会影响解读。",
    "Loads the 2026 fixture structure, match metadata, stage/group labels, selected-match detail, and linked market context.": "加载 2026 赛程结构、比赛 metadata、阶段/小组标签、选中比赛详情和关联市场上下文。",
    "Uses a World Cup map workspace for city-level monitoring, venue context, regional risk, and match-linked inspection.": "使用世界杯地图工作区进行城市级监控、场馆上下文、区域风险和比赛关联检查。",
    "Surfaces host-city weather and operational context that can matter for travel, match conditions, and related prediction markets.": "展示主办城市天气和运营上下文，这些因素可能影响旅行、比赛条件和相关预测市场。",
    "Connects World Cup news, odds references, Polymarket market links, roster/team context, and runtime intelligence feeds.": "连接世界杯新闻、赔率参考、Polymarket 市场链接、阵容/球队上下文和 runtime intelligence feeds。",
    "All variants share the same serving philosophy. Expensive collection and enrichment run outside the browser; the frontend reads normalized endpoints through /wm-api/ ; responses should expose fresh, cached, stale, empty, or degraded states instead of hiding missing data.": "所有版本共享同一种服务理念：昂贵的采集和 enrichment 在浏览器外运行；前端通过 /wm-api/ 读取标准化 endpoints；响应应暴露 fresh、cached、stale、empty 或 degraded 状态，而不是隐藏缺失数据。",
    "All variants share the same serving philosophy. Expensive collection and enrichment run outside the browser; the frontend reads normalized endpoints through": "所有版本共享同一种服务理念：昂贵的采集和 enrichment 在浏览器外运行；前端通过以下路径读取标准化 endpoints：",
    "; responses should expose fresh, cached, stale, empty, or degraded states instead of hiding missing data.": "；响应应暴露 fresh、cached、stale、empty 或 degraded 状态，而不是隐藏缺失数据。",
    "This keeps the platform extensible. A new vertical workspace can reuse the same market identity, OrderFilled, oracle, snapshot, and runtime API layers while changing only the interface and panel composition.": "这让平台保持可扩展。新的垂直工作区可以复用同样的 market identity、OrderFilled、oracle、snapshot 和 runtime API 层，只改变界面和 panel 组合。",
}

TEXT_ZH.update(
    {
        "For production-style hosting, build the frontend locally or on the serving host, publish webpage/dist into the static web root, and keep API process management in systemd.": "生产式部署时，在本地或服务主机上构建前端，将 webpage/dist 发布到静态 Web 根目录，并使用 systemd 管理 API 进程。",
        "Start with": "建议先阅读",
        ", then read": "，然后阅读",
        "for the market, OrderFilled, and oracle data model.": "来理解 market、OrderFilled 和 oracle 数据模型。",
        "The project paper is available on arXiv, and implementation details are published through the GitHub repository.": "项目论文已发布在 arXiv，具体实现细节通过 GitHub 仓库公开。",
        "The frontend reads through /wm-api/ , which lets Nginx route the browser to the active API service without hard-coding private hosts in client code.": "前端通过 /wm-api/ 读取数据，让 Nginx 将浏览器请求路由到当前 active API 服务，而不需要在客户端代码中硬编码私有主机。",
        "The frontend reads through": "前端通过",
        ", which lets Nginx route the browser to the active API service without hard-coding private hosts in client code.": "读取数据，让 Nginx 将浏览器请求路由到当前 active API 服务，而不需要在客户端代码中硬编码私有主机。",
        "The signal layer connects price action with chain flow, liquidity, external content, and generated market context so a move can be reviewed from one surface.": "信号层把价格行为、链上流动、流动性、外部内容和生成式市场上下文连接起来，使一次价格移动可以在同一界面中被复盘。",
        "polyData stores every discoverable Polymarket market identity: Gamma id, slug, condition id, question id, oracle, CLOB token ids, outcome labels, category, tags, created time, and end time.": "polyData 存储每一个可发现的 Polymarket market identity：Gamma id、slug、condition id、question id、oracle、CLOB token ids、outcome labels、category、tags、created time 和 end time。",
        "Runtime data includes active and closed flags, latest prices, 24 hour price change, volume, trade count, best bid/ask, order-book depth, and last activity time.": "Runtime data 包含 active/closed flags、latest prices、24 小时价格变化、volume、trade count、best bid/ask、order-book depth 和 last activity time。",
        "Polygon chain data includes OrderFilled events, maker/taker addresses, side, outcome token, price, size, fee fields, non-trade cashflows, and address-level flow summaries.": "Polygon 链上数据包含 OrderFilled events、maker/taker addresses、side、outcome token、price、size、fee fields、非交易 cashflows 以及地址级 flow summaries。",
        "UMA resolution data includes adapter mappings, question initialization, request/propose/dispute/settle events, proposed price, settled price, transaction hashes, block numbers, and matched market identity.": "UMA resolution data 包含 adapter mappings、question initialization、request/propose/dispute/settle events、proposed price、settled price、transaction hashes、block numbers 和 matched market identity。",
        "External data includes RSS and news feeds, Jin10 macro flashes, BWENews, Yahoo and finance quotes, crypto funding venues, DeFi metrics, weather feeds, sports schedules, ESPN data, and AI-generated market briefs.": "外部数据包含 RSS 与新闻 feeds、Jin10 宏观快讯、BWENews、Yahoo 与金融报价、crypto funding venues、DeFi metrics、天气 feeds、体育赛程、ESPN 数据以及 AI 生成的市场简报。",
        "polyData uses several independent collection paths because no single Polymarket endpoint is complete enough for historical and live analysis. Gamma provides the canonical event and market surface; CLOB provides condition-level market and token data; Polygon RPC provides registry, trade, and oracle truth.": "polyData 使用多条独立采集路径，因为没有任何单一 Polymarket endpoint 足以支撑历史和实时分析。Gamma 提供 canonical event 和 market surface；CLOB 提供 condition-level market 与 token 数据；Polygon RPC 提供 registry、trade 和 oracle truth。",
        "ClickHouse stores high-volume OrderFilled and cashflow data; PostgreSQL stores canonical market, oracle, serving, and status tables; Redis and SQLite snapshots protect the UI from slow or stale upstream calls.": "ClickHouse 存储高吞吐 OrderFilled 与 cashflow 数据；PostgreSQL 存储 canonical market、oracle、serving 和 status 表；Redis 与 SQLite snapshots 用来保护 UI，避免被慢速或陈旧 upstream calls 拖垮。",
        "The sync loop refreshes markets first, then scans oracle and trade blocks with overlap windows. This ordering matters because markets can arrive after chain activity; re-scanning recent blocks lets late-discovered markets attach to already-seen trades and oracle events.": "同步循环会先刷新 markets，再用重叠窗口扫描 oracle 和 trade blocks。这个顺序很重要，因为市场可能在链上活动之后才被发现；重新扫描近期区块可以让后来发现的 markets 关联到已经看到的 trades 和 oracle events。",
        "These panels explain what a Polymarket market is, where it sits in the lifecycle, and whether the displayed price and liquidity are reliable enough for analysis. They are the first panels to check when opening a market workspace.": "这些 panels 解释一个 Polymarket market 是什么、处在生命周期的哪个阶段，以及当前显示价格和流动性是否足以支撑分析。打开市场工作区时应首先查看它们。",
        "The goal is to connect market identity, event context, token identity, price surface, order-book depth, and first-seen discovery signals before moving into flow, oracle, or external context.": "目标是在进入 flow、oracle 或 external context 之前，先把 market identity、event context、token identity、price surface、order-book depth 和 first-seen discovery signals 连接起来。",
        "A market is not just active or inactive. polyData treats it as a lifecycle object: discovered, cataloged, tradeable, active, closing, oracle-reviewed, settled, and archived. These panels provide the identity and price anchors used by the flow and oracle panels.": "market 不只是 active 或 inactive。polyData 将其视为生命周期对象：discovered、cataloged、tradeable、active、closing、oracle-reviewed、settled 和 archived。这些 panels 提供 flow 与 oracle panels 依赖的身份和价格锚点。",
        "These panels explain who is trading, where fills are clustering, whether liquidity is thin or real, and which external trading venues may be pressuring Polymarket probabilities. They are built around OrderFilled rows, wallet flow, cross-market signals, and crypto or finance pressure boards.": "这些 panels 解释谁在交易、fills 聚集在哪里、流动性是真实还是稀薄，以及哪些外部交易场所可能正在影响 Polymarket probabilities。它们围绕 OrderFilled rows、wallet flow、cross-market signals，以及 crypto/finance pressure boards 构建。",
        "These panels explain why a market may be moving and whether its resolution path is clean. They combine oracle state, related news, AI summaries, policy feeds, finance news, technology signals, and domain-specific event feeds.": "这些 panels 解释 market 为什么可能正在移动，以及它的 resolution path 是否清晰。它们组合 oracle state、related news、AI summaries、policy feeds、finance news、technology signals 和 domain-specific event feeds。",
        "Polymarket questions often depend on data outside Polymarket. These panels bring macro releases, inflation components, commodities, weather, sports, and time-zone context into the same workspace as market prices and fills.": "Polymarket 问题经常依赖 Polymarket 之外的数据。这些 panels 将宏观发布、通胀分项、大宗商品、天气、体育和时区上下文带入与 market prices 和 fills 相同的工作区。",
        "The CPI panel family explains what is known before an inflation print, what official data has already been released, and which surrounding macro drivers may move Polymarket inflation, Fed, and growth markets. The goal is not to replace BLS, FRED, or Cleveland Fed data. The goal is to put those sources into an operator dashboard where actuals, forecasts, nowcasts, market context, and source health can be read in one place.": "CPI panel family 解释通胀发布前已知什么、哪些官方数据已经发布，以及哪些周边宏观驱动可能影响 Polymarket 的 inflation、Fed 和 growth markets。目标不是替代 BLS、FRED 或 Cleveland Fed 数据，而是把这些来源放进操作员仪表盘，让 actuals、forecasts、nowcasts、market context 和 source health 可以在一个地方阅读。",
        "polyData separates CPI intelligence into release timing, event estimates, official actuals, component pressure, and related macro driver panels. This matters because a scheduled CPI event can have forecast values before release, actual values after release, component data from prior official prints, and market impact from energy, food, shelter, labor, rates, and growth indicators.": "polyData 将 CPI intelligence 分成 release timing、event estimates、official actuals、component pressure 和 related macro driver panels。这很重要，因为计划中的 CPI event 在发布前可能有 forecast values，发布后才有 actual values，同时还需要前期官方分项数据，以及 energy、food、shelter、labor、rates 和 growth indicators 带来的市场影响。",
        "We present the polyMonitor Forecast Intelligence Graph, a structured multi-agent system for generating auditable prediction-market intelligence from Polymarket data. The system combines deterministic evidence construction with model-backed specialist agents, critique, calibration, and panel generation. Its objective is not to simulate human conversation, but to produce market-level claims that are grounded in prices, fills, related markets, oracle state, and external catalysts.": "我们提出 polyMonitor Forecast Intelligence Graph：一个用于从 Polymarket 数据生成可审计预测市场情报的结构化多 Agent 系统。系统结合确定性证据构造、模型驱动的 specialist agents、critique、calibration 和 panel generation。它的目标不是模拟多人聊天，而是生成以价格、fills、相关市场、oracle state 和外部催化因素为依据的市场级判断。",
        "Figure 1. The Forecast Intelligence Graph separates deterministic state construction from model-backed specialist reasoning, critique, and panel writing.": "图 1. Forecast Intelligence Graph 将确定性状态构造与模型驱动的 specialist reasoning、critique 和 panel writing 分离。",
        "Prediction markets expose probabilistic beliefs through prices, but market interpretation requires more than a last-traded value. A useful monitoring system must jointly reason over order-book microstructure, fill activity, sibling markets, resolution criteria, oracle state, and exogenous information. We formulate polyMonitor as a graph-structured inference system. Given a market-wide evidence packet and a dashboard lens, the graph emits a calibrated panel payload, a quant snapshot, and a replayable node-level audit log. The architecture is intentionally small: deterministic nodes build the evidence state, specialist LLM agents analyze complementary uncertainty surfaces, a skeptic agent performs critique, and a writer agent converts calibrated state into dashboard views.": "预测市场通过价格暴露概率信念，但市场解读不能只依赖最近成交价。一个有用的监控系统必须同时推理订单簿微观结构、fill activity、兄弟市场、结算标准、oracle state 和外生信息。我们将 polyMonitor 形式化为图结构推理系统：给定全市场 evidence packet 和 dashboard lens，图会输出校准后的 panel payload、quant snapshot 和可回放的 node-level audit log。架构刻意保持小而清晰：确定性节点构建 evidence state，specialist LLM agents 分析互补的不确定性表面，skeptic agent 执行 critique，writer agent 将校准状态转成 dashboard views。",
        "The production graph is a directed state machine rather than an open-ended group chat. Its nodes are executed in a fixed order so that later model calls observe compact, typed state instead of an unbounded transcript. The current graph consists of deterministic state builders, three specialist model agents, a rule-based calibration node, a skeptic model agent, and a final panel writer.": "生产图是一个有向状态机，而不是开放式群聊。节点按固定顺序执行，使后续模型调用看到紧凑、类型化的状态，而不是无边界 transcript。当前图包含 deterministic state builders、三个 specialist model agents、一个 rule-based calibration node、一个 skeptic model agent，以及最终 panel writer。",
        "This design follows a research-paper view of multi-agent systems: agents are introduced only when they own distinct sources of uncertainty. Microstructure, catalyst, and resolution reasoning are separated because they fail in different ways. A liquidity signal can be real while the news catalyst is stale; a catalyst can be strong while the market wording makes settlement ambiguous.": "这种设计遵循论文式 multi-agent system 视角：只有当某个 agent 拥有独立不确定性来源时才引入它。Microstructure、catalyst 和 resolution reasoning 被分离，因为它们的失败模式不同。流动性信号可能真实而新闻催化已经过时；催化因素可能很强，但市场措辞可能让结算变得模糊。",
        "The runtime executes the graph as a stateful inference procedure. Deterministic nodes first compress raw inputs into a graph context; model nodes then operate over that context with optional tool traces; final outputs are normalized into panel schemas and stored with replay metadata.": "runtime 将该图作为有状态推理流程执行。确定性节点先把 raw inputs 压缩为 graph context；模型节点随后在该 context 上运行，并可携带 tool traces；最终输出会被规范化到 panel schemas，并和 replay metadata 一起存储。",
        "The dashboard views are not independent agents. They are lenses over a shared graph run. The same evidence packet and node event trace can support overview, special, and trend views, but each lens asks the final writer to emphasize a different decision surface.": "dashboard views 不是独立 agents，而是同一次 graph run 上的不同 lenses。同一个 evidence packet 和 node event trace 可以支持 overview、special 和 trend views，但每个 lens 会要求最终 writer 强调不同决策表面。",
        "The system emits more than a written panel. The output is a triple: the user-facing payload, the quant evidence state, and the replayable event trace. This makes the architecture inspectable under both product and research evaluation.": "系统输出的不只是 written panel，而是一个三元组：user-facing payload、quant evidence state 和可回放 event trace。这让架构在产品和研究评估中都可检查。",
        "A paper-level evaluation should not score the system by prose fluency alone. The relevant question is whether the graph improves market interpretation under uncertainty while preserving calibration and auditability. We evaluate the system along four axes.": "论文级评估不应只按文字流畅度打分。真正问题是该图是否在保持 calibration 和 auditability 的同时，提升不确定条件下的市场解读。我们沿四个轴评估系统。",
        "The current system should be read as a monitoring and interpretation graph, not an autonomous trading or oracle-action agent. It does not submit orders, move liquidity, or initiate UMA disputes. It can surface resolution risk, but it does not replace legal or official settlement review. Its claims remain bounded by data freshness, source coverage, CLOB availability, model reliability, and the quality of the market metadata supplied to the graph.": "当前系统应被理解为 monitoring and interpretation graph，而不是 autonomous trading 或 oracle-action agent。它不会提交订单、移动流动性或发起 UMA disputes。它可以暴露 resolution risk，但不能替代法律或官方 settlement review。其判断受 data freshness、source coverage、CLOB availability、model reliability 以及输入 market metadata 质量约束。",
        "The public endpoints expose the current run artifacts needed for product display and research audit. Browser deployments access these routes through the /wm-api prefix.": "公开 endpoints 暴露产品展示和研究审计所需的当前 run artifacts。浏览器部署通过 /wm-api 前缀访问这些 routes。",
        "Let x denote a market evidence packet containing Polymarket market metadata, prices, order-book summaries, recent fills, related-market candidates, oracle state, external context, and prior forecast memory. Let l be a panel lens in {overview, special, trend} . The system learns no private market model during inference. Instead, it computes a structured mapping:": "令 x 表示一个 market evidence packet，其中包含 Polymarket market metadata、prices、order-book summaries、recent fills、related-market candidates、oracle state、external context 和 prior forecast memory。令 l 表示 {overview, special, trend} 中的一个 panel lens。系统在推理期间不学习私有市场模型，而是计算一个结构化映射：",
        "where y is a dashboard-ready intelligence payload, q is a quant and data-quality snapshot, and e is an ordered event trace containing node outputs, input hashes, model identifiers, latency, token usage, tool traces, and errors. The central design constraint is auditability: every generated claim should be attributable to a bounded input packet or an explicit model-backed reasoning node.": "其中 y 是可直接用于仪表盘的 intelligence payload，q 是 quant 与 data-quality snapshot，e 是有序 event trace，包含 node outputs、input hashes、model identifiers、latency、token usage、tool traces 和 errors。核心设计约束是可审计性：每条 generated claim 都应能归因到有界 input packet 或明确的模型推理节点。",
    }
)

TEXT_ZH.update(
    {
        "For production-style hosting, build the frontend locally or on the serving host, publish": "生产式部署时，在本地或服务主机上构建前端，将",
        "into the static web root, and keep API process management in systemd.": "发布到静态 Web 根目录，并使用 systemd 管理 API 进程。",
        "Upcoming CPI release event, reference month, release time, and countdown.": "即将发布的 CPI event、reference month、release time 和 countdown。",
        "Cleveland Fed nowcast values for CPI and PCE buckets when available.": "可用时展示 Cleveland Fed 对 CPI 与 PCE buckets 的 nowcast values。",
        "Latest BLS/FRED CPI series values, previous values, and released actuals after the official event time.": "最新 BLS/FRED CPI series values、previous values，以及官方发布时间后的 released actuals。",
        "Energy, gasoline, diesel, food, shelter, goods, and services pressure rows.": "Energy、gasoline、diesel、food、shelter、goods 和 services pressure rows。",
        "Goods, tariff, labor, services, Fed, rates, growth, and recession-risk rows.": "Goods、tariff、labor、services、Fed、rates、growth 和 recession-risk rows。",
        "Coverage counts, source count, row count, cache mode, and degraded source flags.": "Coverage counts、source count、row count、cache mode 和 degraded source flags。",
        "CPI panels use public macro sources and seed caches. A value can be official, forecast, nowcast, or registry context, so the panel displays source labels and metadata rather than presenting every number as the same kind of truth.": "CPI panels 使用公开宏观来源和 seed caches。一个值可能是 official、forecast、nowcast 或 registry context，因此 panel 会显示 source labels 和 metadata，而不是把每个数字都当成同一种事实。",
        "CPI actuals, previous values, index levels, month-over-month, year-over-year, and selected component rows.": "CPI actuals、previous values、index levels、month-over-month、year-over-year 和选中的 component rows。",
        "CPI release timing, reference period, and scheduled release time.": "CPI release timing、reference period 和 scheduled release time。",
        "Energy, gasoline, diesel, and WTI pressure used in headline CPI context.": "用于 headline CPI context 的 energy、gasoline、diesel 和 WTI pressure。",
        "Cross-market latest on-chain OrderFilled activity with market, side, price, size, and recency context.": "跨市场最新链上 OrderFilled activity，包含 market、side、price、size 和 recency context。",
        "Use it as the live tape for the whole platform, then pivot into the specific market that is moving.": "作为整个平台的实时 tape 使用，再跳转到正在移动的具体 market。",
        "Largest recent on-chain trades, emphasizing high-notional or high-impact wallet behavior.": "近期最大链上 trades，突出 high-notional 或 high-impact wallet behavior。",
        "Use it to detect concentrated flow that may not be obvious from price history alone.": "用于发现仅靠 price history 不明显的集中 flow。",
        "Oracle-adjacent and large live trade flow that deserves extra review.": "值得额外复核的 oracle-adjacent 与 large live trade flow。",
        "Use it when a market is near close, near resolution, or moving on unusual fill activity.": "适用于 market 临近 close、resolution，或因异常 fill activity 移动时。",
        "Smart-money flow, wallet history, and explainable market briefs.": "Smart-money flow、wallet history 和可解释 market briefs。",
        "Use it to connect wallet activity with a readable market thesis instead of staring at raw addresses.": "用于把 wallet activity 连接到可读 market thesis，而不是只盯着原始地址。",
        "Cross-source heuristic signal stack combining price action, flow, external content, and market context.": "跨来源 heuristic signal stack，组合 price action、flow、external content 和 market context。",
        "Use it as a triage layer: it does not replace research, but it tells you which markets deserve attention first.": "作为 triage layer 使用：它不替代研究，但能告诉你哪些 markets 最值得先看。",
        "Live active Polymarket markets with category, timing, status, and tradeability context.": "实时 active Polymarket markets，包含 category、timing、status 和 tradeability context。",
        "Use it as the discovery surface for markets that are currently relevant, liquid, or newly active.": "将它作为发现当前相关、有流动性或刚活跃 markets 的入口。",
        "First-seen markets with early YES probability and initial metadata from the market catalog.": "首次发现的 markets，包含早期 YES probability 和 market catalog 中的初始 metadata。",
        "Use it to catch newly listed markets before the rest of the dashboard has accumulated deep history.": "用于在仪表盘积累深度历史之前捕捉新上市 markets。",
        "Active CPI, Fed, growth, labor, and energy market clusters from Polymarket.": "来自 Polymarket 的 active CPI、Fed、growth、labor 和 energy market clusters。",
        "Use it to move from one isolated market into the surrounding macro question family.": "用于从单个孤立 market 跳转到周围宏观问题族。",
        "Identifiers, category, timing, status, outcome context, and current pricing for the selected market.": "选中 market 的 identifiers、category、timing、status、outcome context 和 current pricing。",
        "Use it to confirm that the question, slug, condition id, event, and displayed outcome match the market you intend to inspect.": "用于确认 question、slug、condition id、event 和 displayed outcome 是否匹配你想检查的 market。",
        "Resolution rules, tags, oracle references, and human-readable context for the focused market.": "focused market 的 resolution rules、tags、oracle references 和人类可读上下文。",
        "Use it before interpreting price movement; many Polymarket errors come from misunderstanding the resolution rule.": "在解读价格变化前使用；许多 Polymarket 误判来自对 resolution rule 的误解。",
        "Constructs the initial evidence state from market candidates, groups, prices, fills, oracle snippets, external context, and search results.": "从 market candidates、groups、prices、fills、oracle snippets、external context 和 search results 构造初始 evidence state。",
        "Links sibling markets, event-level groups, deadline ladders, adjacent outcomes, and cross-market relationships that may reveal inconsistent pricing.": "连接 sibling markets、event-level groups、deadline ladders、adjacent outcomes 和 cross-market relationships，用来发现不一致定价。",
        "Computes price drift, fill-tape microstructure, spread indicators, related-market scores, and data-quality warnings.": "计算 price drift、fill-tape microstructure、spread indicators、related-market scores 和 data-quality warnings。",
        "Loads prior forecast episodes and summary lessons so current reasoning can be compared with historical failures and successes.": "加载 prior forecast episodes 和 summary lessons，使当前推理可以与历史失败和成功案例比较。",
        "Analyzes named markets through implied probability, volume, trade count, fill concentration, bid/ask quality, close probabilities, and liquidity caveats.": "通过 implied probability、volume、trade count、fill concentration、bid/ask quality、close probabilities 和 liquidity caveats 分析指定 markets。",
        "Identifies external triggers, related-market catalysts, event timing, and evidence that would plausibly move market-implied probability.": "识别 external triggers、related-market catalysts、event timing，以及可能推动 market-implied probability 的证据。",
        "Examines market wording, deadline buckets, official-source hierarchy, oracle signals, ambiguity, and settlement risk.": "检查 market wording、deadline buckets、official-source hierarchy、oracle signals、ambiguity 和 settlement risk。",
        "Anchors confidence to market-implied prices, data warnings, related-market stress, prior Brier history, and specialist confidence.": "将 confidence 锚定到 market-implied prices、data warnings、related-market stress、prior Brier history 和 specialist confidence。",
        "Challenges weak evidence, missing price-change data, stale signals, narrative overreach, and probability miscalibration.": "质疑 weak evidence、missing price-change data、stale signals、narrative overreach 和 probability miscalibration。",
        "Writes the final panel payload from evidence, specialist reports, calibration state, and memory without introducing ungrounded claims.": "根据 evidence、specialist reports、calibration state 和 memory 写出最终 panel payload，同时避免引入无依据判断。",
    }
)

TEXT_ZH.update(
    {
        "cpi-release-command-center is the event panel for the next CPI print. It combines release calendar data, nowcast or forecast data, and official actual series when the event has passed. Before the release, the panel can show forecast or nowcast values while actual fields remain empty. After the scheduled official release time, the panel can populate actual values from BLS/FRED series.": "cpi-release-command-center 是下次 CPI 发布的事件面板。它组合 release calendar data、nowcast 或 forecast data，以及事件结束后的 official actual series。发布前，panel 可以显示 forecast 或 nowcast，而 actual 字段保持为空；到达官方发布时间后，panel 可以从 BLS/FRED series 填充 actual values。",
        "Shown as empty before release; empty actual is expected before official publication.": "发布前显示为空；官方公布前 actual 为空是预期状态。",
        "Actual minus forecast, displayed in percentage points where applicable.": "actual 减 forecast；适用时以百分点显示。",
        "cpi-components-pressure-registry explains which parts of the inflation basket are creating pressure. It aggregates energy, food, shelter, goods, and related CPI component rows into one registry. Rows show value labels, change labels, source labels, age labels, and a tone such as hot, cool, watch, or neutral.": "cpi-components-pressure-registry 解释通胀篮子的哪些部分正在形成压力。它将 energy、food、shelter、goods 和相关 CPI component rows 聚合到一个 registry 中；每行展示 value labels、change labels、source labels、age labels，以及 hot、cool、watch 或 neutral 等 tone。",
        "Energy shocks can move headline CPI quickly and can affect inflation-market probabilities.": "Energy shocks 可以快速推动 headline CPI，并影响 inflation-market probabilities。",
        "Food rows explain basket pressure that may not be visible from headline CPI alone.": "Food rows 解释仅靠 headline CPI 不一定能看出的 basket pressure。",
        "The CPI registry also exposes focused driver panels. These are not all CPI actuals. They are macro context panels designed to explain why CPI or Fed markets may move.": "CPI registry 还暴露 focused driver panels。它们不全是 CPI actuals，而是 macro context panels，用来解释 CPI 或 Fed markets 为什么可能移动。",
        "Goods inflation, import-price, tariff, supply-chain, and Federal Register policy context.": "Goods inflation、import-price、tariff、supply-chain 和 Federal Register policy context。",
        "Fed calendar, rates context, yield-curve spread, growth demand, sentiment, industrial production, and recession-risk rows.": "Fed calendar、rates context、yield-curve spread、growth demand、sentiment、industrial production 和 recession-risk rows。",
        "The CPI system labels data by role. This avoids treating a pre-release nowcast as an official actual, and it avoids treating a cached runtime snapshot as a new source.": "CPI 系统按角色标记数据，避免把发布前 nowcast 当作 official actual，也避免把 cached runtime snapshot 当作新的数据源。",
        "Actual means an official released value from BLS/FRED or another official public source.": "Actual 指来自 BLS/FRED 或其他官方公开来源的已发布数值。",
        "Forecast means an estimate used before release, currently mapped mainly from the Cleveland Fed nowcast registry for CPI/PCE buckets.": "Forecast 指发布前使用的估计值，目前主要从 Cleveland Fed nowcast registry 映射 CPI/PCE buckets。",
        "Previous means the previous official value or latest known value used as a baseline.": "Previous 指作为基准使用的前一个官方值或最新已知值。",
        "Redis seed and SQLite seed mean cached snapshots used to make the dashboard fast; the row source still identifies the real upstream source.": "Redis seed 和 SQLite seed 指用于加速仪表盘的 cached snapshots；row source 仍会标识真实 upstream source。",
        "Coverage counts how many upstream source checks are usable for the panel; it is source health, not a count of CPI releases.": "Coverage 统计该 panel 有多少 upstream source checks 可用；它是 source health，不是 CPI 发布次数。",
        "Most CPI registry rows share the same runtime schema so the frontend can render them consistently.": "大多数 CPI registry rows 共享同一套 runtime schema，使前端可以一致渲染。",
        "The dashboard requests CPI registry panels from the runtime panel API. A typical query uses the panel ids below.": "仪表盘通过 runtime panel API 请求 CPI registry panels。典型查询使用下面这些 panel ids。",
        "Use these ids when debugging payloads or comparing the docs against the live dashboard.": "调试 payload 或对照 live dashboard 检查文档时使用这些 ids。",
        "Recent oracle events across markets, including resolution-adjacent activity.": "跨市场近期 oracle events，包含 resolution-adjacent activity。",
        "Use it to verify whether market status is still trading-driven or already in an oracle/resolution phase.": "用于验证 market status 仍由交易驱动，还是已经进入 oracle/resolution phase。",
        "Use it to spot clusters of related markets moving around the same catalyst or narrative.": "用于发现围绕同一 catalyst 或 narrative 移动的 related market clusters。",
        "Use it to connect a probability move to a source, headline, or external event without leaving the workspace.": "用于在不离开工作区的情况下，将 probability move 连接到 source、headline 或 external event。",
        "AI lab release, benchmark, pricing, valuation, outage, and regulation signal feed.": "AI lab release、benchmark、pricing、valuation、outage 和 regulation signal feed。",
        "Use it for AI-company and model-release markets where product news moves probability quickly.": "用于 AI-company 和 model-release markets，尤其是 product news 会快速推动 probability 的场景。",
        "Use it to anchor tech-market narratives in live equity context rather than only headline flow.": "用于把 tech-market narratives 锚定到 live equity context，而不只是 headline flow。",
        "Use it for consumer internet markets where adoption, ranking, bans, or platform changes matter.": "用于 adoption、ranking、bans 或 platform changes 重要的 consumer internet markets。",
        "Use it to detect TradFi analyst catalysts that may affect equity-linked or sector-linked markets.": "用于发现可能影响 equity-linked 或 sector-linked markets 的 TradFi analyst catalysts。",
        "Use it to identify protocol-specific security shocks before they appear in broader crypto prices.": "用于在更广泛 crypto prices 反映之前识别 protocol-specific security shocks。",
        "High-signal trade policy watchlist mapped to tariffs, flows, barriers, revenue, and strategic controls.": "高信号 trade policy watchlist，映射到 tariffs、flows、barriers、revenue 和 strategic controls。",
        "Use it when market movement depends on duties, export controls, sanctions, or supply-chain rerouting.": "当 market movement 依赖 duties、export controls、sanctions 或 supply-chain rerouting 时使用。",
        "Use it to connect global event risk to commodity, macro, election, and security markets.": "用于把 global event risk 连接到 commodity、macro、election 和 security markets。",
        "Use it for fast macro headlines that can move rates, inflation, FX, and commodity questions.": "用于可能推动 rates、inflation、FX 和 commodity questions 的快速宏观 headline。",
        "Use it as a compact live news feed for fast-moving event surfaces that need headline triage.": "作为紧凑 live news feed，用于需要 headline triage 的 fast-moving event surfaces。",
        "Official release calendar plus nowcast registry for CPI, PCE, and Fed timing.": "CPI、PCE 和 Fed timing 的官方 release calendar 与 nowcast registry。",
        "Use it to understand which CPI component may be driving headline or core inflation probabilities.": "用于理解哪个 CPI component 可能正在驱动 headline 或 core inflation probabilities。",
        "Official CPI, PCE, NFP, and FOMC release timing with Polymarket implied CPI baseline.": "官方 CPI、PCE、NFP 和 FOMC release timing，并带 Polymarket implied CPI baseline。",
        "Use it as a neutral nowcast reference when market odds diverge from public model estimates.": "当 market odds 偏离公开模型估计时，作为中性 nowcast reference。",
        "Use it to track tradable goods inflation pressure and policy-driven import cost shocks.": "用于追踪 tradable goods inflation pressure 和政策驱动的 import cost shocks。",
        "Use it to judge whether services inflation risk is coming from wages, labor tightness, or claims data.": "用于判断 services inflation risk 是否来自 wages、labor tightness 或 claims data。",
        "Official CPI food-component pressure for inflation market positioning.": "用于 inflation market positioning 的官方 CPI food-component pressure。",
        "Use it when goods inflation markets depend on shipping, import, tariff, or substitution pressure.": "当 goods inflation markets 依赖 shipping、import、tariff 或 substitution pressure 时使用。",
        "Use it to evaluate whether macro market odds reflect soft-landing or recession pressure.": "用于评估 macro market odds 反映的是 soft-landing 还是 recession pressure。",
        "Use it to anchor energy, metals, food, and inflation markets in live commodity prices.": "用于把 energy、metals、food 和 inflation markets 锚定到 live commodity prices。",
        "Commodity shocks mapped into equity beneficiaries, cost-pressure names, spread-watch names, and related Polymarket themes.": "将 commodity shocks 映射到 equity beneficiaries、cost-pressure names、spread-watch names 和相关 Polymarket themes。",
        "Live market clocks for Shanghai, New York, London, and the selected weather city.": "上海、纽约、伦敦和选中天气城市的 live market clocks。",
        "Use it to interpret whether a market is moving during an active session or during thin after-hours liquidity.": "用于判断 market 是在 active session 中移动，还是在 after-hours 薄流动性中移动。",
        "Live global city temperatures, forecast highs, and Polymarket quote coverage.": "全球城市实时温度、forecast highs 和 Polymarket quote coverage。",
        "Use it as the overview table for weather-linked markets and city-level temperature risk.": "作为 weather-linked markets 和 city-level temperature risk 的概览表。",
        "Grouped Polymarket weather markets across temperature, precipitation, storms, climate, and disaster families.": "按 temperature、precipitation、storms、climate 和 disaster families 分组的 Polymarket weather markets。",
        "Use it to find the relevant market family before opening a city or quote detail panel.": "用于在打开 city 或 quote detail panel 前找到相关 market family。",
        "Use it when market odds may be reacting to storms, heat waves, warnings, or local forecast changes.": "当 market odds 可能对 storms、heat waves、warnings 或 local forecast changes 反应时使用。",
        "ESPN BPI win probability, matchup quality, projected margin, and expected score.": "ESPN BPI win probability、matchup quality、projected margin 和 expected score。",
        "GRID official esports series state with local Polymarket matching context.": "GRID 官方 esports series state，并带本地 Polymarket matching context。",
    }
)

TEXT_ZH.update(
    {
        "Each model-backed node receives a system role, a compact JSON user packet, and a required output schema. The prompt does not ask the model to forecast from memory. It asks the model to inspect a bounded evidence state and return compact JSON containing findings, risks, watch items, confidence, and probability-adjustment notes. The specialist roles are intentionally asymmetric:": "每个模型驱动节点都会接收 system role、紧凑 JSON user packet 和必需 output schema。prompt 不要求模型凭记忆预测，而是要求模型检查有界 evidence state，并返回包含 findings、risks、watch items、confidence 和 probability-adjustment notes 的紧凑 JSON。specialist roles 被刻意设计为不对称：",
        "Skeptic: stale evidence, unsupported causal claims, missing data, hallucination risk, and overconfident probability shifts.": "Skeptic：stale evidence、unsupported causal claims、missing data、hallucination risk 和 overconfident probability shifts。",
        "Category rotation, attention migration, catalyst clusters, and whether isolated event interest is becoming a broader trend.": "category rotation、attention migration、catalyst clusters，以及孤立事件关注是否正在变成更广泛趋势。",
        "The input packet is deliberately heterogeneous. Polymarket interpretation depends on market identity, trading activity, sibling markets, and settlement semantics. The graph therefore keeps the following surfaces separate rather than collapsing them into one prose context.": "input packet 被刻意设计为异构。Polymarket 解读依赖 market identity、trading activity、sibling markets 和 settlement semantics。因此图会保持以下 surfaces 分离，而不是把它们折叠成一个 prose context。",
        "Calibration: compare probability statements or confidence bins against realized market resolutions using Brier score, expected calibration error, and category-level reliability curves.": "Calibration：使用 Brier score、expected calibration error 和 category-level reliability curves，将 probability statements 或 confidence bins 与实际 market resolutions 比较。",
        "Discrimination: measure whether high-confidence claims separate from low-confidence claims under subsequent price movement, resolution outcome, or analyst review.": "Discrimination：衡量 high-confidence claims 是否能在后续 price movement、resolution outcome 或 analyst review 中与 low-confidence claims 区分开。",
        "Faithfulness: audit whether each generated claim is supported by the evidence packet, a specialist output, a tool trace, or an explicit uncertainty statement.": "Faithfulness：审计每条 generated claim 是否由 evidence packet、specialist output、tool trace 或明确 uncertainty statement 支撑。",
        "The graph is designed to be evaluated by removing or replacing components. Useful ablations include deterministic-only output, no specialist agents, no skeptic, no reflexion memory, no related-market state, and writer-only generation. These ablations reveal whether accuracy comes from data construction, specialist reasoning, critique, or final writing.": "该图被设计为可通过移除或替换组件进行评估。有效消融包括 deterministic-only output、no specialist agents、no skeptic、no reflexion memory、no related-market state 和 writer-only generation。这些消融用于揭示准确性来自数据构造、specialist reasoning、critique 还是 final writing。",
        "The design is closest to specialized financial and forecasting-agent systems that use structured roles, bounded context, and explicit evaluation. The useful lesson is not that more agents are always better, but that decomposition can improve interpretability when each agent owns a distinct evidence surface.": "该设计最接近使用结构化角色、有界上下文和明确评估的专业金融/预测 agent systems。真正有用的结论不是 agent 越多越好，而是当每个 agent 拥有独立 evidence surface 时，分解可以提升可解释性。",
        "TradingAgents : financial workflow with analysts, bull/bear researchers, trader synthesis, risk review, and final decision control.": "TradingAgents：包含 analysts、bull/bear researchers、trader synthesis、risk review 和 final decision control 的金融工作流。",
        "ForesightFlow : coordination layer, calibration, discriminative power, and cost-quality evaluation for forecasting agents.": "ForesightFlow：面向 forecasting agents 的 coordination layer、calibration、discriminative power 和 cost-quality evaluation。",
        "MASAI , SciAgents , and DrugAgent : task-specific systems that use short trajectories, explicit responsibilities, structured inputs and outputs, and domain knowledge instead of open-ended chat.": "MASAI、SciAgents 和 DrugAgent：使用短轨迹、明确职责、结构化输入输出和领域知识的任务专用系统，而不是开放式聊天。",
    }
)

PHRASE_ZH = [
    ("Use it to", "用于"),
    ("Use it as", "可作为"),
    ("Use it when", "适用于"),
    ("Use it before", "用于在"),
    ("Use it for", "用于"),
    ("Market-wide", "全市场"),
    ("market-wide", "全市场"),
]


def doc_dirs() -> list[Path]:
    return sorted(
        p.parent for p in DOCS.glob("*/index.html") if p.parent.name != "zh"
    )


def encoded_doc_path(name: str, zh: bool = False) -> str:
    encoded = quote(name)
    return f"/docs/zh/{encoded}/" if zh else f"/docs/{encoded}/"


def zh_path_for_href(href: str) -> str:
    if not href.startswith("/docs/") or href.startswith("/docs/zh/"):
        return href
    return "/docs/zh/" + href[len("/docs/") :]


def en_path_for_href(href: str) -> str:
    if not href.startswith("/docs/zh/"):
        return href
    return "/docs/" + href[len("/docs/zh/") :]


def update_css_version(soup: BeautifulSoup) -> None:
    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href", "")
        if href.startswith("/docs-page.css"):
            link["href"] = f"/docs-page.css?v={CSS_VERSION}"


def page_title(name: str, zh: bool) -> str:
    if name in TITLE_ZH:
        return TITLE_ZH[name] if zh else name
    return name


def install_alternates(soup: BeautifulSoup, slug: str, zh: bool) -> None:
    head = soup.head
    if not head:
        return
    for old in head.find_all("link", rel="alternate"):
        old.decompose()
    en_href = encoded_doc_path(slug, zh=False)
    zh_href = encoded_doc_path(slug, zh=True)
    en_link = soup.new_tag("link", rel="alternate", hreflang="en", href=en_href)
    zh_link = soup.new_tag("link", rel="alternate", hreflang="zh-CN", href=zh_href)
    head.append(en_link)
    head.append(zh_link)


def install_lang_switch(soup: BeautifulSoup, slug: str, zh: bool) -> None:
    nav = soup.select_one(".docs-nav")
    if not nav:
        return
    for old in nav.select(".docs-lang-switch"):
        old.decompose()
    theme = nav.select_one(".docs-theme-toggle")
    switch = soup.new_tag(
        "a",
        href=encoded_doc_path(slug, zh=not zh),
        **{"class": "docs-lang-switch", "aria-label": "Switch language"},
    )
    switch.string = "EN" if zh else "ZH"
    if theme:
        theme.insert_before(switch)
    else:
        nav.append(switch)


def translate_text(text: str) -> str:
    stripped = " ".join(text.split())
    if not stripped:
        return text
    if stripped in TEXT_ZH:
        translated = TEXT_ZH[stripped]
    elif stripped in TITLE_ZH:
        translated = TITLE_ZH[stripped]
    else:
        translated = stripped
        if any("a" <= ch.lower() <= "z" for ch in translated):
            for src, dst in PHRASE_ZH:
                translated = translated.replace(src, dst)
    if text.startswith(" ") and not translated.startswith(" "):
        translated = " " + translated
    if text.endswith(" ") and not translated.endswith(" "):
        translated = translated + " "
    return translated


def translate_dom(soup: BeautifulSoup) -> None:
    if soup.html:
        soup.html["lang"] = "zh-CN"
    if soup.title and soup.title.string:
        title = soup.title.string.replace(" - polyData Documentation", "")
        soup.title.string = f"{page_title(title, zh=True)} - polyData 文档"
    desc = soup.find("meta", attrs={"name": "description"})
    if desc and desc.get("content"):
        desc["content"] = translate_text(desc["content"])
    for el in soup.find_all(["p", "li", "td", "th", "figcaption"]):
        full_text = " ".join(el.get_text(" ", strip=True).split())
        if full_text in TEXT_ZH and el.find(["a", "code", "strong", "em", "span"]):
            el.clear()
            el.append(TEXT_ZH[full_text])
    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if not parent or parent.name in SKIP_TAGS:
            continue
        if parent.find_parent(SKIP_TAGS):
            continue
        new_text = translate_text(str(node))
        if new_text != str(node):
            node.replace_with(new_text)
    for el in soup.find_all(attrs={"aria-label": True}):
        label = el.get("aria-label")
        if label:
            el["aria-label"] = translate_text(label)


def rewrite_links(soup: BeautifulSoup, zh: bool) -> None:
    for a in soup.find_all("a", href=True):
        a["href"] = zh_path_for_href(a["href"]) if zh else en_path_for_href(a["href"])
    for link in soup.find_all("link", href=True):
        rel = link.get("rel") or []
        if "canonical" in rel:
            link["href"] = zh_path_for_href(link["href"]) if zh else en_path_for_href(link["href"])
    refresh = soup.find("meta", attrs={"http-equiv": "refresh"})
    if refresh and refresh.get("content"):
        content = refresh["content"]
        if "url=/docs/" in content:
            url = content.split("url=", 1)[1]
            refresh["content"] = "0; url=" + (zh_path_for_href(url) if zh else en_path_for_href(url))


def update_english_page(path: Path, slug: str) -> None:
    soup = BeautifulSoup(path.read_text(), "html.parser")
    update_css_version(soup)
    install_lang_switch(soup, slug, zh=False)
    install_alternates(soup, slug, zh=False)
    path.write_text(str(soup), encoding="utf-8")


def write_zh_page(path: Path, slug: str) -> None:
    soup = BeautifulSoup(path.read_text(), "html.parser")
    update_css_version(soup)
    rewrite_links(soup, zh=True)
    translate_dom(soup)
    install_lang_switch(soup, slug, zh=True)
    install_alternates(soup, slug, zh=True)
    out_dir = DOCS / "zh" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(str(soup), encoding="utf-8")


def write_zh_root() -> None:
    root = DOCS / "zh"
    root.mkdir(exist_ok=True)
    (root / "index.html").write_text(
        """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta http-equiv="refresh" content="0; url=/docs/zh/documentation/" />
    <title>polyData 文档</title>
    <link rel="canonical" href="/docs/zh/documentation/" />
  </head>
  <body>
    <p><a href="/docs/zh/documentation/">打开 polyData 文档</a></p>
  </body>
</html>
""",
        encoding="utf-8",
    )


def main() -> None:
    for page_dir in doc_dirs():
        slug = page_dir.name
        page = page_dir / "index.html"
        update_english_page(page, slug)
        write_zh_page(page, slug)
    write_zh_root()


if __name__ == "__main__":
    main()
