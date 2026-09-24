#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Environment-backed external data source configuration.

All upstream website/API URLs used by polyData should be read through this
module or through scripts.api.config, not hard-coded in feature code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = Path(__file__).resolve().parent


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / ".env.local",
        SCRIPTS_ROOT / ".env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    text = str(value).strip().strip('"').strip("'")
    return text or default


_load_dotenv_files()

# Polygon reads are intentionally pinned to the explicitly configured
# Polymonitor endpoint.  NODE_URL used to point at a hosted Chainstack
# fallback; silently falling back to it makes local collectors switch
# providers when the self-hosted SSH tunnel is unavailable.
POLYGON_RPC_URL = env_str("POLYMARKET_RPC_URL")
POLYMARKET_GAMMA_API_BASE = env_str("POLYDATA_GAMMA_API_BASE")
POLYMARKET_MACRO_MAP_SOURCE_URL = env_str("POLYDATA_MACRO_MARKET_MAP_SOURCE_URL")
POLYMARKET_DATA_API_BASE = env_str("POLYDATA_POLYMARKET_DATA_API_BASE")
POLYMARKET_ACTIVITY_API_URL = env_str("POLYDATA_POLYMARKET_ACTIVITY_API_URL")
POLYMARKET_CLOB_API_BASE = env_str("POLYDATA_CLOB_API_BASE")
POLYMARKET_CLOB_WS_URL = env_str("POLYDATA_CLOB_WS_URL")

YAHOO_CHART_BASE_URL = env_str("POLYDATA_YAHOO_CHART_BASE_URL")
COINGECKO_BASE_URL = env_str("POLYDATA_COINGECKO_BASE_URL")
CLEVELAND_FED_NOWCAST_URL = env_str("POLYDATA_CLEVELAND_FED_NOWCAST_URL")
CPI_CALENDAR_BLS_CPI_URL = env_str("POLYDATA_CPI_CALENDAR_BLS_CPI_URL")
CPI_CALENDAR_BLS_EMPLOYMENT_URL = env_str("POLYDATA_CPI_CALENDAR_BLS_EMPLOYMENT_URL")
CPI_CALENDAR_BEA_SCHEDULE_URL = env_str("POLYDATA_CPI_CALENDAR_BEA_SCHEDULE_URL")
CPI_CALENDAR_FOMC_URL = env_str("POLYDATA_CPI_CALENDAR_FOMC_URL")
CPI_CALENDAR_SOURCE_URL = env_str("POLYDATA_CPI_CALENDAR_SOURCE_URL")
ENERGY_SHOCK_WTI_XLS_URL = env_str("POLYDATA_ENERGY_SHOCK_WTI_XLS_URL")
ENERGY_SHOCK_GASOLINE_XLS_URL = env_str("POLYDATA_ENERGY_SHOCK_GASOLINE_XLS_URL")
ENERGY_SHOCK_DIESEL_XLS_URL = env_str("POLYDATA_ENERGY_SHOCK_DIESEL_XLS_URL")
ENERGY_SHOCK_SOURCE_URL = env_str("POLYDATA_ENERGY_SHOCK_SOURCE_URL")
FOOD_BASKET_FRED_CSV_URL_TEMPLATE = env_str("POLYDATA_FOOD_BASKET_FRED_CSV_URL_TEMPLATE")
FOOD_BASKET_SOURCE_URL = env_str("POLYDATA_FOOD_BASKET_SOURCE_URL")
GEO_SHOCK_OFAC_SDN_URL = env_str("POLYDATA_GEO_SHOCK_OFAC_SDN_URL")
GEO_SHOCK_OFAC_CONSOLIDATED_URL = env_str("POLYDATA_GEO_SHOCK_OFAC_CONSOLIDATED_URL")
GEO_SHOCK_FEDERAL_REGISTER_API_URL = env_str("POLYDATA_GEO_SHOCK_FEDERAL_REGISTER_API_URL")
GEO_SHOCK_CONFLICT_API_URL = env_str("POLYDATA_GEO_SHOCK_CONFLICT_API_URL")
GEO_SHOCK_GDELT_DOC_API_URL = env_str("POLYDATA_GEO_SHOCK_GDELT_DOC_API_URL")
GEO_SHOCK_UCDP_API_URL = env_str("POLYDATA_GEO_SHOCK_UCDP_API_URL") or env_str("UCDP_API_URL")
GEO_SHOCK_UCDP_ACCESS_TOKEN = (
    env_str("POLYDATA_GEO_SHOCK_UCDP_ACCESS_TOKEN")
    or env_str("UCDP_API_TOKEN")
    or env_str("UCDP_API_Token")
    or env_str("UCDP_ACCESS_TOKEN")
    or env_str("UC_DP_KEY")
)
GEO_SHOCK_ACLED_TOKEN_URL = env_str("POLYDATA_GEO_SHOCK_ACLED_TOKEN_URL")
GEO_SHOCK_ACLED_API_URL = env_str("POLYDATA_GEO_SHOCK_ACLED_API_URL")
GEO_SHOCK_ACLED_EMAIL = env_str("POLYDATA_GEO_SHOCK_ACLED_EMAIL") or env_str("ACLED_USERNAME")
GEO_SHOCK_ACLED_PASSWORD = env_str("POLYDATA_GEO_SHOCK_ACLED_PASSWORD") or env_str("ACLED_PASSWORD")
GEO_SHOCK_SOURCE_URL = env_str("POLYDATA_GEO_SHOCK_SOURCE_URL")
CRYPTO_FUNDING_WATCH_API_URL = env_str("POLYDATA_CRYPTO_FUNDING_WATCH_API_URL")
CRYPTO_FUNDING_WATCH_BYBIT_API_URL = env_str("POLYDATA_CRYPTO_FUNDING_WATCH_BYBIT_API_URL")
CRYPTO_FUNDING_WATCH_SOURCE_URL = env_str("POLYDATA_CRYPTO_FUNDING_WATCH_SOURCE_URL")
GRID_OPEN_ACCESS_BASE_URL = env_str("POLYDATA_GRID_OPEN_ACCESS_BASE_URL")
GRID_CENTRAL_DATA_GRAPHQL_URL = env_str("POLYDATA_GRID_CENTRAL_DATA_GRAPHQL_URL")
GRID_SERIES_STATE_GRAPHQL_URL = env_str("POLYDATA_GRID_SERIES_STATE_GRAPHQL_URL")
GRID_SOURCE_URL = env_str("POLYDATA_GRID_SOURCE_URL")
THE_ODDS_API_BASE_URL = env_str("POLYDATA_THE_ODDS_API_BASE_URL")
THE_ODDS_SOURCE_URL = env_str("POLYDATA_THE_ODDS_SOURCE_URL")
OPEN_METEO_API_URL = env_str("POLYDATA_OPEN_METEO_API_URL")
AVIATIONWEATHER_METAR_API_URL = env_str("POLYDATA_AVIATIONWEATHER_METAR_API_URL")
GOOGLE_NEWS_RSS_URL = env_str("POLYDATA_GOOGLE_NEWS_RSS_URL")
WEATHER_SOURCE_URL = env_str("POLYDATA_WEATHER_SOURCE_URL")

FINANCE_DEFILLAMA_YIELDS_URL = env_str("POLYDATA_FINANCE_DEFILLAMA_YIELDS_URL")
FINANCE_ALTERNATIVE_FNG_URL = env_str("POLYDATA_FINANCE_ALTERNATIVE_FNG_URL")
FINANCE_GOOGLE_NEWS_RSS_URL = env_str("POLYDATA_FINANCE_GOOGLE_NEWS_RSS_URL")
FINANCE_YAHOO_CHART_URL_TEMPLATE = env_str("POLYDATA_FINANCE_YAHOO_CHART_URL_TEMPLATE")
FINANCE_FRED_CSV_URL_TEMPLATE = env_str("POLYDATA_FINANCE_FRED_CSV_URL_TEMPLATE")
FINANCE_BARCHART_QUOTE_URL_TEMPLATE = env_str("POLYDATA_FINANCE_BARCHART_QUOTE_URL_TEMPLATE")
FINANCE_CNN_FNG_URL = env_str("POLYDATA_FINANCE_CNN_FNG_URL")
FINANCE_CNN_FNG_REFERER_URL = env_str("POLYDATA_FINANCE_CNN_FNG_REFERER_URL")
FINANCE_AAII_SENTIMENT_URL = env_str("POLYDATA_FINANCE_AAII_SENTIMENT_URL")
FINANCE_BROKER_RESEARCH_EDISON_URL = env_str("POLYDATA_FINANCE_BROKER_RESEARCH_EDISON_URL")
FINANCE_BROKER_RESEARCH_ZACKS_URL = env_str("POLYDATA_FINANCE_BROKER_RESEARCH_ZACKS_URL")
FINANCE_BROKER_RESEARCH_WATER_TOWER_URL = env_str("POLYDATA_FINANCE_BROKER_RESEARCH_WATER_TOWER_URL")
FINANCE_BROKER_RESEARCH_EASTMONEY_URL = env_str("POLYDATA_FINANCE_BROKER_RESEARCH_EASTMONEY_URL")
FINANCE_BROKER_RESEARCH_CHOICE_URL = env_str("POLYDATA_FINANCE_BROKER_RESEARCH_CHOICE_URL")
FINANCE_HYPERLIQUID_INFO_URL = env_str("POLYDATA_FINANCE_HYPERLIQUID_INFO_URL")
FINANCE_OKX_MARKET_TICKER_URL = env_str("POLYDATA_FINANCE_OKX_MARKET_TICKER_URL")
FINANCE_DEFILLAMA_STABLECOINS_URL = env_str("POLYDATA_FINANCE_DEFILLAMA_STABLECOINS_URL")
FINANCE_CFTC_LEGACY_COT_URL = env_str("POLYDATA_FINANCE_CFTC_LEGACY_COT_URL")

TECH_GOOGLE_NEWS_RSS_URL = env_str("POLYDATA_TECH_GOOGLE_NEWS_RSS_URL")
TECH_APP_STORE_TOP_FREE_URL = env_str("POLYDATA_TECH_APP_STORE_TOP_FREE_URL")

ESPN_NBA_BASE_URL = env_str("POLYDATA_ESPN_NBA_BASE_URL")
ESPN_CORE_NBA_BASE_URL = env_str("POLYDATA_ESPN_CORE_NBA_BASE_URL")
ESPN_RSS_NEWS_URL = env_str("POLYDATA_RSS_ESPN_NEWS_URL", "https://www.espn.com/espn/rss/news")
NBA_LINEUPS_BASE_URL = env_str("POLYDATA_NBA_LINEUPS_BASE_URL")
NBA_OFFICIAL_BASE_URL = env_str("POLYDATA_NBA_OFFICIAL_BASE_URL")

JIN10_FLASH_API_URL = env_str("POLYDATA_JIN10_FLASH_API_URL")
JIN10_FLASH_DETAIL_BASE_URL = env_str("POLYDATA_JIN10_FLASH_DETAIL_BASE_URL")
JIN10_LIVE_URL = env_str("POLYDATA_JIN10_LIVE_URL")

F1_BWENEWS_RSS_URL = env_str("POLYDATA_F1_BWENEWS_RSS_URL")
F1_BWENEWS_SOURCE_URL = env_str("POLYDATA_F1_BWENEWS_SOURCE_URL")

RSS_FEEDS: List[Dict[str, str]] = [
    {
        "source": "BBC World",
        "url": env_str("POLYDATA_RSS_BBC_WORLD_URL", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        "category": "World",
    },
    {
        "source": "BBC Politics",
        "url": env_str("POLYDATA_RSS_BBC_POLITICS_URL", "https://feeds.bbci.co.uk/news/politics/rss.xml"),
        "category": "Politics",
    },
    {
        "source": "Guardian World",
        "url": env_str("POLYDATA_RSS_GUARDIAN_WORLD_URL", "https://www.theguardian.com/world/rss"),
        "category": "World",
    },
    {
        "source": "NPR News",
        "url": env_str("POLYDATA_RSS_NPR_NEWS_URL", "https://feeds.npr.org/1001/rss.xml"),
        "category": "US",
    },
    {
        "source": "PBS NewsHour",
        "url": env_str("POLYDATA_RSS_PBS_NEWSHOUR_URL", "https://www.pbs.org/newshour/feeds/rss/headlines"),
        "category": "US",
    },
    {
        "source": "ABC News",
        "url": env_str("POLYDATA_RSS_ABC_NEWS_URL", "https://feeds.abcnews.com/abcnews/topstories"),
        "category": "US",
    },
    {
        "source": "CBS News",
        "url": env_str("POLYDATA_RSS_CBS_NEWS_URL", "https://www.cbsnews.com/latest/rss/main"),
        "category": "US",
    },
    {
        "source": "NBC News",
        "url": env_str("POLYDATA_RSS_NBC_NEWS_URL", "https://feeds.nbcnews.com/nbcnews/public/news"),
        "category": "US",
    },
    {
        "source": "Axios",
        "url": env_str("POLYDATA_RSS_AXIOS_URL", "https://api.axios.com/feed/"),
        "category": "US",
    },
    {
        "source": "AP News",
        "url": env_str("POLYDATA_RSS_AP_NEWS_URL", "https://news.google.com/rss/search?q=site:apnews.com+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "World",
    },
    {
        "source": "CNN World",
        "url": env_str("POLYDATA_RSS_CNN_WORLD_URL", "https://news.google.com/rss/search?q=site:cnn.com+world+news+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "World",
    },
    {
        "source": "France 24",
        "url": env_str("POLYDATA_RSS_FRANCE24_URL", "https://www.france24.com/en/rss"),
        "category": "World",
    },
    {
        "source": "Euronews",
        "url": env_str("POLYDATA_RSS_EURONEWS_URL", "https://www.euronews.com/rss?format=xml"),
        "category": "World",
    },
    {
        "source": "DW News",
        "url": env_str("POLYDATA_RSS_DW_NEWS_URL", "https://rss.dw.com/xml/rss-en-all"),
        "category": "World",
    },
    {
        "source": "Reuters World",
        "url": env_str("POLYDATA_RSS_REUTERS_WORLD_URL", "https://news.google.com/rss/search?q=site:reuters.com+world+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "World",
    },
    {
        "source": "Reuters Business",
        "url": env_str("POLYDATA_RSS_REUTERS_BUSINESS_URL", "https://news.google.com/rss/search?q=site:reuters.com+business+markets+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Finance",
    },
    {
        "source": "Politico",
        "url": env_str("POLYDATA_RSS_POLITICO_URL", "https://rss.politico.com/politics-news.xml"),
        "category": "Politics",
    },
    {
        "source": "The Hill",
        "url": env_str("POLYDATA_RSS_THE_HILL_URL", "https://thehill.com/news/feed/"),
        "category": "Politics",
    },
    {
        "source": "White House",
        "url": env_str("POLYDATA_RSS_WHITE_HOUSE_URL", "https://www.whitehouse.gov/briefings-statements/feed/"),
        "category": "Government",
    },
    {
        "source": "Federal Reserve",
        "url": env_str("POLYDATA_RSS_FEDERAL_RESERVE_URL", "https://www.federalreserve.gov/feeds/press_all.xml"),
        "category": "Government",
    },
    {
        "source": "SEC",
        "url": env_str("POLYDATA_RSS_SEC_URL", "https://www.sec.gov/news/pressreleases.rss"),
        "category": "Government",
    },
    {
        "source": "UN News",
        "url": env_str("POLYDATA_RSS_UN_NEWS_URL", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
        "category": "Government",
    },
    {
        "source": "CISA",
        "url": env_str("POLYDATA_RSS_CISA_URL", "https://www.cisa.gov/cybersecurity-advisories/all.xml"),
        "category": "Government",
    },
    {
        "source": "Al Jazeera",
        "url": env_str("POLYDATA_RSS_AL_JAZEERA_URL", "https://www.aljazeera.com/xml/rss/all.xml"),
        "category": "World",
    },
    {
        "source": "CNBC",
        "url": env_str("POLYDATA_RSS_CNBC_URL", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        "category": "Finance",
    },
    {
        "source": "Yahoo Finance",
        "url": env_str("POLYDATA_RSS_YAHOO_FINANCE_URL", "https://finance.yahoo.com/news/rssindex"),
        "category": "Finance",
    },
    {
        "source": "Financial Times",
        "url": env_str("POLYDATA_RSS_FINANCIAL_TIMES_URL", "https://www.ft.com/rss/home"),
        "category": "Finance",
    },
    {
        "source": "Seeking Alpha",
        "url": env_str("POLYDATA_RSS_SEEKING_ALPHA_URL", "https://seekingalpha.com/market_currents.xml"),
        "category": "Finance",
    },
    {
        "source": "MarketWatch",
        "url": env_str("POLYDATA_RSS_MARKETWATCH_URL", "https://news.google.com/rss/search?q=site:marketwatch.com+markets+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Finance",
    },
    {
        "source": "Bond Market",
        "url": env_str("POLYDATA_RSS_BOND_MARKET_URL", "https://news.google.com/rss/search?q=(%22bond+market%22+OR+%22treasury+yield%22+OR+%22fixed+income%22)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Finance",
    },
    {
        "source": "Options Market",
        "url": env_str("POLYDATA_RSS_OPTIONS_MARKET_URL", "https://news.google.com/rss/search?q=(%22options+market%22+OR+VIX+OR+%22market+volatility%22)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Finance",
    },
    {
        "source": "Economic Data",
        "url": env_str("POLYDATA_RSS_ECONOMIC_DATA_URL", "https://news.google.com/rss/search?q=(CPI+OR+inflation+OR+GDP+OR+%22jobs+report%22+OR+unemployment)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Macro",
    },
    {
        "source": "Oil & Gas",
        "url": env_str("POLYDATA_RSS_OIL_GAS_URL", "https://news.google.com/rss/search?q=(oil+price+OR+OPEC+OR+%22natural+gas%22+OR+pipeline+OR+LNG+OR+WTI+OR+Brent)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Energy",
    },
    {
        "source": "Reuters Energy",
        "url": env_str("POLYDATA_RSS_REUTERS_ENERGY_URL", "https://news.google.com/rss/search?q=site:reuters.com+energy+oil+OPEC+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Energy",
    },
    {
        "source": "Gold & Metals",
        "url": env_str("POLYDATA_RSS_GOLD_METALS_URL", "https://news.google.com/rss/search?q=(%22gold+price%22+OR+%22silver+price%22+OR+%22copper+price%22+OR+%22precious+metals%22)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Commodities",
    },
    {
        "source": "CoinDesk",
        "url": env_str("POLYDATA_RSS_COINDESK_URL", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        "category": "Crypto",
    },
    {
        "source": "Cointelegraph",
        "url": env_str("POLYDATA_RSS_COINTELEGRAPH_URL", "https://cointelegraph.com/rss"),
        "category": "Crypto",
    },
    {
        "source": "Decrypt",
        "url": env_str("POLYDATA_RSS_DECRYPT_URL", "https://decrypt.co/feed"),
        "category": "Crypto",
    },
    {
        "source": "The Block",
        "url": env_str("POLYDATA_RSS_THE_BLOCK_URL", "https://news.google.com/rss/search?q=site:theblock.co+when:1d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Crypto",
    },
    {
        "source": "Blockworks",
        "url": env_str("POLYDATA_RSS_BLOCKWORKS_URL", "https://blockworks.co/feed"),
        "category": "Crypto",
    },
    {
        "source": "The Defiant",
        "url": env_str("POLYDATA_RSS_THE_DEFIANT_URL", "https://thedefiant.io/feed"),
        "category": "Crypto",
    },
    {
        "source": "Bitcoin Magazine",
        "url": env_str("POLYDATA_RSS_BITCOIN_MAGAZINE_URL", "https://bitcoinmagazine.com/feed"),
        "category": "Crypto",
    },
    {
        "source": "Stablecoin Policy",
        "url": env_str("POLYDATA_RSS_STABLECOIN_POLICY_URL", "https://news.google.com/rss/search?q=(stablecoin+regulation+OR+%22stablecoin+bill%22+OR+USDT+OR+USDC)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Crypto",
    },
    {
        "source": "Polymarket News",
        "url": env_str("POLYDATA_RSS_POLYMARKET_NEWS_URL", "https://news.google.com/rss/search?q=(Polymarket+OR+%22prediction+market%22+OR+%22prediction+markets%22)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Prediction Markets",
    },
    {
        "source": "Election Markets",
        "url": env_str("POLYDATA_RSS_ELECTION_MARKETS_URL", "https://news.google.com/rss/search?q=(election+polls+OR+presidential+election+OR+nominee+OR+campaign)+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Elections",
    },
    {
        "source": "Geopolitics Markets",
        "url": env_str("POLYDATA_RSS_GEOPOLITICS_MARKETS_URL", "https://news.google.com/rss/search?q=(war+ceasefire+sanctions+Iran+Ukraine+China+Taiwan)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Geopolitics",
    },
    {
        "source": "Macro Markets",
        "url": env_str("POLYDATA_RSS_MACRO_MARKETS_URL", "https://news.google.com/rss/search?q=(Fed+inflation+CPI+rates+jobs+oil+markets)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Macro",
    },
    {
        "source": "Tech Markets",
        "url": env_str("POLYDATA_RSS_TECH_MARKETS_URL", "https://news.google.com/rss/search?q=(AI+OpenAI+Nvidia+Tesla+Elon+technology)+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Tech",
    },
    {
        "source": "TechCrunch",
        "url": env_str("POLYDATA_RSS_TECHCRUNCH_URL", "https://techcrunch.com/feed/"),
        "category": "Tech",
    },
    {
        "source": "The Verge",
        "url": env_str("POLYDATA_RSS_THE_VERGE_URL", "https://www.theverge.com/rss/index.xml"),
        "category": "Tech",
    },
    {
        "source": "MIT Tech Review",
        "url": env_str("POLYDATA_RSS_MIT_TECH_REVIEW_URL", "https://www.technologyreview.com/feed/"),
        "category": "Tech",
    },
    {
        "source": "VentureBeat AI",
        "url": env_str("POLYDATA_RSS_VENTUREBEAT_AI_URL", "https://venturebeat.com/category/ai/feed/"),
        "category": "Tech",
    },
    {
        "source": "ArXiv AI",
        "url": env_str("POLYDATA_RSS_ARXIV_AI_URL", "https://export.arxiv.org/rss/cs.AI"),
        "category": "Research",
    },
    {
        "source": "Semiconductor News",
        "url": env_str("POLYDATA_RSS_SEMICONDUCTOR_NEWS_URL", "https://news.google.com/rss/search?q=(semiconductor+OR+chip+OR+TSMC+OR+NVIDIA+OR+Intel)+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Tech",
    },
    {
        "source": "ESPN",
        "url": ESPN_RSS_NEWS_URL,
        "category": "Sports",
    },
    {
        "source": "Cricket Markets",
        "url": env_str("POLYDATA_RSS_CRICKET_MARKETS_URL", "https://news.google.com/rss/search?q=(%22Indian+Premier+League%22+OR+IPL+OR+cricket+OR+Rajasthan+Royals+OR+Lucknow+Super+Giants)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "Esports Markets",
        "url": env_str("POLYDATA_RSS_ESPORTS_MARKETS_URL", "https://news.google.com/rss/search?q=(esports+OR+Valorant+OR+%22Counter-Strike%22+OR+%22League+of+Legends%22+OR+Dota+OR+KR%C3%9C+OR+KRU)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Esports",
    },
    {
        "source": "HLTV Counter-Strike",
        "url": env_str("POLYDATA_RSS_HLTV_COUNTER_STRIKE_URL", "https://news.google.com/rss/search?q=site:hltv.org+Counter-Strike+CS2+esports+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Esports",
    },
    {
        "source": "Valorant Esports",
        "url": env_str("POLYDATA_RSS_VALORANT_ESPORTS_URL", "https://news.google.com/rss/search?q=(Valorant+VCT+esports+match+roster)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Esports",
    },
    {
        "source": "LoL Esports",
        "url": env_str("POLYDATA_RSS_LOL_ESPORTS_URL", "https://news.google.com/rss/search?q=(%22League+of+Legends%22+LCK+LPL+LEC+esports+match)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Esports",
    },
    {
        "source": "Dot Esports",
        "url": env_str("POLYDATA_RSS_DOT_ESPORTS_URL", "https://dotesports.com/feed"),
        "category": "Esports",
    },
    {
        "source": "Sports Markets",
        "url": env_str("POLYDATA_RSS_SPORTS_MARKETS_URL", "https://news.google.com/rss/search?q=(NBA+NFL+MLB+NHL+UFC+esports+injury+odds)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "NBA Markets",
        "url": env_str("POLYDATA_RSS_NBA_MARKETS_URL", "https://news.google.com/rss/search?q=(NBA+injury+lineup+odds+Knicks+Celtics+Thunder+Finals)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "Tennis Markets",
        "url": env_str("POLYDATA_RSS_TENNIS_MARKETS_URL", "https://news.google.com/rss/search?q=(tennis+ATP+WTA+injury+odds+tournament)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "MLB Markets",
        "url": env_str("POLYDATA_RSS_MLB_MARKETS_URL", "https://news.google.com/rss/search?q=(MLB+pitcher+lineup+injury+odds)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "UFC Markets",
        "url": env_str("POLYDATA_RSS_UFC_MARKETS_URL", "https://news.google.com/rss/search?q=(UFC+fight+injury+weigh-in+odds)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "NBA Official",
        "url": env_str("POLYDATA_RSS_NBA_OFFICIAL_NEWS_URL", "https://news.google.com/rss/search?q=site:nba.com+NBA+injury+lineup+finals+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "ESPN NBA",
        "url": env_str("POLYDATA_RSS_ESPN_NBA_NEWS_URL", "https://news.google.com/rss/search?q=site:espn.com/nba+NBA+injury+lineup+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "The Athletic NBA",
        "url": env_str("POLYDATA_RSS_ATHLETIC_NBA_NEWS_URL", "https://news.google.com/rss/search?q=site:nytimes.com/athletic+NBA+injury+lineup+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Sports",
    },
    {
        "source": "Reuters Politics",
        "url": env_str("POLYDATA_RSS_REUTERS_POLITICS_URL", "https://news.google.com/rss/search?q=site:reuters.com+US+politics+election+campaign+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Politics",
    },
    {
        "source": "AP Politics",
        "url": env_str("POLYDATA_RSS_AP_POLITICS_URL", "https://news.google.com/rss/search?q=site:apnews.com+politics+election+campaign+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Politics",
    },
    {
        "source": "Election Polling",
        "url": env_str("POLYDATA_RSS_ELECTION_POLLING_URL", "https://news.google.com/rss/search?q=(election+polling+OR+%22presidential+poll%22+OR+%22senate+poll%22+OR+FiveThirtyEight)+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Elections",
    },
    {
        "source": "Campaign Finance",
        "url": env_str("POLYDATA_RSS_CAMPAIGN_FINANCE_URL", "https://news.google.com/rss/search?q=(campaign+fundraising+OR+FEC+OR+super+PAC+OR+election+spending)+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Elections",
    },
    {
        "source": "Crypto Regulation",
        "url": env_str("POLYDATA_RSS_CRYPTO_REGULATION_URL", "https://news.google.com/rss/search?q=(crypto+regulation+OR+SEC+bitcoin+ETF+OR+stablecoin+bill)+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Crypto",
    },
    {
        "source": "Crypto Markets",
        "url": env_str("POLYDATA_RSS_CRYPTO_MARKETS_URL", "https://news.google.com/rss/search?q=(bitcoin+ethereum+solana+crypto+market+ETF+flows)+when:1d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Crypto",
    },
    {
        "source": "Coinbase Blog",
        "url": env_str("POLYDATA_RSS_COINBASE_BLOG_URL", "https://www.coinbase.com/blog/rss"),
        "category": "Crypto",
    },
    {
        "source": "OpenAI News",
        "url": env_str("POLYDATA_RSS_OPENAI_NEWS_URL", "https://openai.com/news/rss.xml"),
        "category": "Tech",
    },
    {
        "source": "Google AI",
        "url": env_str("POLYDATA_RSS_GOOGLE_AI_NEWS_URL", "https://news.google.com/rss/search?q=site:blog.google/technology/ai+Google+AI+Gemini+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Tech",
    },
    {
        "source": "Anthropic News",
        "url": env_str("POLYDATA_RSS_ANTHROPIC_NEWS_URL", "https://news.google.com/rss/search?q=site:anthropic.com/news+Claude+Anthropic+when:7d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Tech",
    },
    {
        "source": "AI Markets",
        "url": env_str("POLYDATA_RSS_AI_MARKETS_URL", "https://news.google.com/rss/search?q=(OpenAI+Anthropic+Gemini+Nvidia+AI+model+LLM)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Research",
    },
    {
        "source": "Big Tech Markets",
        "url": env_str("POLYDATA_RSS_BIG_TECH_MARKETS_URL", "https://news.google.com/rss/search?q=(Apple+Microsoft+Google+Amazon+Meta+Tesla+Nvidia)+earnings+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Tech",
    },
    {
        "source": "NHC Advisories",
        "url": env_str("POLYDATA_RSS_NHC_ADVISORIES_URL", "https://www.nhc.noaa.gov/index-at.xml"),
        "category": "Weather",
    },
    {
        "source": "NOAA Weather",
        "url": env_str("POLYDATA_RSS_NOAA_WEATHER_URL", "https://news.google.com/rss/search?q=site:noaa.gov+weather+hurricane+storm+temperature+when:3d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Weather",
    },
    {
        "source": "Weather Markets",
        "url": env_str("POLYDATA_RSS_WEATHER_MARKETS_URL", "https://news.google.com/rss/search?q=(hurricane+storm+heat+wave+temperature+rainfall+snowfall)+when:2d&hl=en-US&gl=US&ceid=US:en"),
        "category": "Weather",
    },
]


CONTENT_TOPIC_REGISTRY: List[Dict[str, object]] = [
    {
        "id": "prediction-markets",
        "label": "Prediction Markets",
        "categories": ["Prediction Markets"],
        "keywords": ["polymarket", "prediction market", "prediction markets", "kalshi", "forecasting"],
        "queries": [
            'Polymarket OR "prediction market" OR Kalshi when:3d',
            '"prediction markets" crypto politics sports when:7d',
        ],
    },
    {
        "id": "politics",
        "label": "Politics",
        "categories": ["Politics", "Government", "US"],
        "keywords": ["politics", "congress", "senate", "house", "trump", "biden", "white house"],
        "queries": [
            "US politics Congress White House election campaign when:2d",
            "Trump Biden Senate House Supreme Court policy when:2d",
        ],
    },
    {
        "id": "elections",
        "label": "Elections",
        "categories": ["Elections", "Politics"],
        "keywords": ["election", "poll", "polls", "nominee", "presidential", "senate race", "campaign"],
        "queries": [
            'election polls presidential campaign nominee "latest poll" when:2d',
            'senate poll governor poll primary campaign fundraising when:3d',
        ],
    },
    {
        "id": "sports",
        "label": "Sports",
        "categories": ["Sports"],
        "keywords": ["sports", "nba", "nfl", "mlb", "nhl", "ufc", "tennis", "cricket", "injury", "lineup"],
        "queries": [
            "NBA NFL MLB NHL UFC tennis cricket injury lineup odds when:1d",
            "sports injury report lineup starting pitcher odds when:1d",
        ],
    },
    {
        "id": "esports",
        "label": "Esports",
        "categories": ["Esports", "Sports"],
        "keywords": ["esports", "counter-strike", "counter strike", "cs2", "valorant", "league of legends", "dota", "kru", "1win", "betclic", "hltv"],
        "queries": [
            'esports Counter-Strike CS2 Valorant League of Legends Dota match roster when:3d',
            'site:hltv.org Counter-Strike CS2 esports match when:7d',
            'Valorant VCT esports match roster odds when:7d',
        ],
    },
    {
        "id": "nba",
        "label": "NBA",
        "categories": ["Sports"],
        "keywords": ["nba", "basketball", "knicks", "celtics", "thunder", "finals", "injury report"],
        "queries": [
            "NBA injury report lineup finals odds when:1d",
            "site:espn.com/nba NBA injury lineup when:2d",
            "site:nba.com NBA injury report finals when:2d",
        ],
    },
    {
        "id": "crypto",
        "label": "Crypto",
        "categories": ["Crypto"],
        "keywords": ["crypto", "bitcoin", "btc", "ethereum", "eth", "stablecoin", "solana", "defi"],
        "queries": [
            "crypto bitcoin ethereum stablecoin ETF regulation market when:1d",
            "bitcoin ETF flows ethereum solana defi when:1d",
        ],
    },
    {
        "id": "finance",
        "label": "Finance",
        "categories": ["Finance"],
        "keywords": ["stocks", "equities", "treasury", "yield", "market", "s&p", "nasdaq", "fed"],
        "queries": [
            "stocks treasury yields S&P Nasdaq Fed markets when:1d",
            "earnings market volatility VIX rates equities when:1d",
        ],
    },
    {
        "id": "macro",
        "label": "Macro",
        "categories": ["Macro", "Finance"],
        "keywords": ["macro", "inflation", "fed", "rates", "jobs", "unemployment", "gdp", "recession"],
        "queries": [
            "Fed inflation rates jobs GDP recession macro when:2d",
            "treasury yields unemployment GDP retail sales when:2d",
        ],
    },
    {
        "id": "cpi",
        "label": "CPI",
        "categories": ["Macro"],
        "keywords": ["cpi", "inflation", "core cpi", "headline cpi", "shelter", "oer"],
        "queries": [
            'CPI inflation "core CPI" shelter OER when:7d',
            '"inflation report" "consumer prices" Fed when:7d',
        ],
    },
    {
        "id": "oil-energy",
        "label": "Oil & Energy",
        "categories": ["Energy", "Commodities", "Macro"],
        "keywords": ["oil", "wti", "crude", "brent", "opec", "gas", "lng", "energy"],
        "queries": [
            'WTI "crude oil" Brent OPEC natural gas LNG when:2d',
            'oil prices energy markets gasoline diesel when:2d',
        ],
    },
    {
        "id": "geopolitics",
        "label": "Geopolitics",
        "categories": ["Geopolitics", "World", "Government"],
        "keywords": ["geopolitics", "war", "ceasefire", "sanctions", "iran", "israel", "ukraine", "russia", "taiwan"],
        "queries": [
            "war ceasefire sanctions Iran Israel Ukraine Russia Taiwan when:2d",
            "geopolitics conflict sanctions defense diplomacy when:2d",
        ],
    },
    {
        "id": "tech",
        "label": "Tech",
        "categories": ["Tech"],
        "keywords": ["tech", "technology", "apple", "tesla", "nvidia", "semiconductor", "chip", "spacex"],
        "queries": [
            "technology Apple Tesla Nvidia semiconductor chips when:2d",
            "big tech earnings antitrust product launch when:3d",
        ],
    },
    {
        "id": "ai",
        "label": "AI",
        "categories": ["Tech", "Research"],
        "keywords": ["ai", "openai", "anthropic", "gemini", "llm", "nvidia", "artificial intelligence"],
        "queries": [
            'AI OpenAI Anthropic Gemini LLM Nvidia "artificial intelligence" when:2d',
            'AI model launch benchmark regulation chips when:3d',
        ],
    },
    {
        "id": "weather",
        "label": "Weather",
        "categories": ["Weather", "World"],
        "keywords": ["weather", "hurricane", "storm", "temperature", "rain", "snow", "heat wave"],
        "queries": [
            "weather hurricane storm temperature heat wave when:2d",
            "NOAA hurricane forecast extreme weather rainfall snowfall when:2d",
        ],
    },
]


def non_empty_feeds(feeds: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [feed for feed in feeds if str(feed.get("url") or "").strip()]


def content_topic_registry() -> List[Dict[str, object]]:
    return list(CONTENT_TOPIC_REGISTRY)
