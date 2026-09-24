"""Natural-hazard aggregation for the World Event Map."""

from .service import DEFAULT_EVENT_LIMIT, get_natural_hazards_snapshot
from .map_feed import (
    DETAIL_SCHEMA_VERSION,
    MAP_SCHEMA_VERSION,
    MAP_SOURCE_KEYS,
    compact_hazard_event,
    get_natural_hazard_event_detail,
    get_natural_hazard_map_snapshot,
    simplify_geometry,
)
from .market_linker import (
    DEFAULT_LIMIT as DEFAULT_RELATED_MARKET_LIMIT,
    related_weather_markets_snapshot,
)

__all__ = [
    "DEFAULT_EVENT_LIMIT",
    "DEFAULT_RELATED_MARKET_LIMIT",
    "DETAIL_SCHEMA_VERSION",
    "MAP_SCHEMA_VERSION",
    "MAP_SOURCE_KEYS",
    "compact_hazard_event",
    "get_natural_hazard_event_detail",
    "get_natural_hazard_map_snapshot",
    "get_natural_hazards_snapshot",
    "related_weather_markets_snapshot",
    "simplify_geometry",
]
