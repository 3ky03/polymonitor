WITH time_bounds AS (
    SELECT 
        CAST('{{start_time}}' AS TIMESTAMP) AS start_time, 
        CAST('{{end_time}}' AS TIMESTAMP) AS end_time,   
        0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d AS polymarket_adapter
),

-- 🌟 新增核心：提取官方适配器映射表 (无时间边界，确保长线市场也能匹配) 🌟
adapter_mapping AS (
    SELECT 
        MAX(questionID) AS real_question_id, 
        ancillaryData
    FROM polymarket_polygon.UmaCtfAdapter_evt_QuestionInitialized
    GROUP BY ancillaryData
),

request_v2 AS (
    SELECT 
        evt_block_number, evt_block_time, evt_index, evt_tx_hash, requester, identifier,
        NULL AS proposer, NULL AS disputer, NULL AS proposedPrice, NULL AS price, NULL AS payout,
        timestamp AS request_timestamp, ancillaryData
    FROM uma_polygon.OptimisticOracleV2_evt_RequestPrice
    CROSS JOIN time_bounds
    WHERE evt_block_time >= start_time AND evt_block_time < end_time
      AND requester = polymarket_adapter
),

propose_v2 AS (
    SELECT 
        evt_block_number, evt_block_time, evt_index, evt_tx_hash, requester, identifier,
        proposer, NULL AS disputer, proposedPrice, NULL AS price, NULL AS payout,
        timestamp AS request_timestamp, ancillaryData
    FROM uma_polygon.OptimisticOracleV2_evt_ProposePrice
    CROSS JOIN time_bounds
    WHERE evt_block_time >= start_time AND evt_block_time < end_time
      AND requester = polymarket_adapter
),

dispute_v2 AS (
    SELECT 
        evt_block_number, evt_block_time, evt_index, evt_tx_hash, requester, identifier,
        proposer, disputer, proposedPrice, NULL AS price, NULL AS payout,
        timestamp AS request_timestamp, ancillaryData
    FROM uma_polygon.OptimisticOracleV2_evt_DisputePrice
    CROSS JOIN time_bounds
    WHERE evt_block_time >= start_time AND evt_block_time < end_time
      AND requester = polymarket_adapter
),

settle_v2 AS (
    SELECT 
        evt_block_number, evt_block_time, evt_index, evt_tx_hash, requester, identifier,
        proposer, disputer, NULL AS proposedPrice, price, payout,
        timestamp AS request_timestamp, ancillaryData
    FROM uma_polygon.OptimisticOracleV2_evt_Settle 
    CROSS JOIN time_bounds
    WHERE evt_block_time >= start_time AND evt_block_time < end_time
      AND requester = polymarket_adapter
),

combine AS (
    SELECT *, 'request' AS label FROM request_v2
    UNION ALL
    SELECT *, 'propose' AS label FROM propose_v2
    UNION ALL
    SELECT *, 'dispute' AS label FROM dispute_v2
    UNION ALL
    SELECT *, 'settle' AS label FROM settle_v2
),

organize AS (
    SELECT
        c.evt_block_number, c.evt_block_time, c.evt_tx_hash, c.label,
        c.requester, c.proposer, c.disputer, c.proposedPrice, c.price, c.payout,
        c.request_timestamp, c.ancillaryData, c.identifier,
        m.real_question_id, -- 从映射表中引入真实的 ID
        from_utf8(c.ancillaryData) AS text_raw
    FROM combine c
    -- 左连接映射表，依据是独一无二的 ancillaryData 文本
    LEFT JOIN adapter_mapping m 
        ON c.ancillaryData = m.ancillaryData
)

SELECT 
    evt_block_number AS block_number,
    
    -- 时间戳格式化对齐 "%Y-%m-%d %H:%M:%S.000 UTC"
    substr(cast(evt_block_time as varchar), 1, 19) || '.000 UTC' AS event_time,
    CAST(evt_tx_hash AS VARCHAR) AS tx_hash,
    label AS event_status,
    
    -- 🌟 完美绑定：优先使用适配器里的官方 ID，如果没有（极端情况）则退回使用 UMA 的 identifier 🌟
    COALESCE(CAST(real_question_id AS VARCHAR), CAST(identifier AS VARCHAR)) AS condition_id,
    COALESCE(CAST(real_question_id AS VARCHAR), CAST(identifier AS VARCHAR)) AS question_id,

    COALESCE(regexp_extract(text_raw, '(?s)description:\s*(.*?)(?:\s*market_id:|$)', 1), '') AS description,
    
    COALESCE(CAST(CAST(regexp_extract(text_raw, 'p1:\s*([0-9]+)', 1) AS int) AS VARCHAR), '') AS p1,
    COALESCE(CAST(CAST(regexp_extract(text_raw, 'p2:\s*([0-9]+)', 1) AS int) AS VARCHAR), '') AS p2,

    LOWER(COALESCE(MAX(CAST(requester AS VARCHAR)) OVER(PARTITION BY ancillaryData, request_timestamp), '')) AS requester,
    LOWER(COALESCE(MAX(CAST(proposer AS VARCHAR)) OVER(PARTITION BY ancillaryData, request_timestamp), '')) AS proposer,
    LOWER(COALESCE(MAX(CAST(disputer AS VARCHAR)) OVER(PARTITION BY ancillaryData, request_timestamp), '0x0000000000000000000000000000000000000000')) AS disputer,
    LOWER(COALESCE(MAX(CAST(proposer AS VARCHAR)) OVER(PARTITION BY ancillaryData, request_timestamp), '')) AS settlement_recipient,
    
    -- 聚合并保留空字符串
    COALESCE(MAX(CASE WHEN label = 'request' THEN CAST(evt_tx_hash AS VARCHAR) END) OVER(PARTITION BY ancillaryData, request_timestamp), '') AS request_transaction,
    COALESCE(MAX(CASE WHEN label = 'propose' THEN CAST(evt_tx_hash AS VARCHAR) END) OVER(PARTITION BY ancillaryData, request_timestamp), '') AS proposal_transaction,
    COALESCE(MAX(CASE WHEN label = 'settle'  THEN CAST(evt_tx_hash AS VARCHAR) END) OVER(PARTITION BY ancillaryData, request_timestamp), '') AS settlement_transaction,

    -- 价格转化为浮点数后变为字符串 (例如 "0.0")
    COALESCE(CAST(MAX(CASE WHEN label IN ('propose', 'dispute') THEN CAST(proposedPrice AS DOUBLE) / 1e18 END) OVER(PARTITION BY ancillaryData, request_timestamp) AS VARCHAR), '') AS proposed_price,
    COALESCE(CAST(MAX(CASE WHEN label = 'settle' THEN CAST(price AS DOUBLE) / 1e18 END) OVER(PARTITION BY ancillaryData, request_timestamp) AS VARCHAR), '') AS settled_price,
    COALESCE(CAST(MAX(payout) OVER(PARTITION BY ancillaryData, request_timestamp) AS VARCHAR), '') AS payout,

    -- 切片前 500 个字符
    substr(text_raw, 1, 500) AS string_raw

FROM organize
ORDER BY evt_block_time DESC
ORDER BY evt_block_time DESC