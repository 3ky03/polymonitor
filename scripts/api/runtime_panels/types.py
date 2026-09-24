from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, cast

from api.context import resolve_route_callable, resolve_route_value


PanelPayload = Dict[str, Any]
MarketSymbol = tuple[str, str, str]


@dataclass(frozen=True)
class FinanceRuntimePanelDependencies:
    watch_panel_snapshot: Callable[..., PanelPayload]
    crypto_funding_watch_snapshot: Callable[..., PanelPayload]
    defi_token_watch_snapshot: Callable[..., PanelPayload]
    market_atlas_snapshot: Callable[..., PanelPayload]
    equity_event_command_snapshot: Callable[..., PanelPayload]
    onchain_tradfi_perp_radar_snapshot: Callable[..., PanelPayload]
    liquidity_regime_snapshot: Callable[..., PanelPayload]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> FinanceRuntimePanelDependencies:
        return cls(
            watch_panel_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_finance_watch_panel_snapshot"),
            ),
            crypto_funding_watch_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_crypto_funding_watch_snapshot"),
            ),
            defi_token_watch_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_defi_token_watch_snapshot"),
            ),
            market_atlas_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_finance_market_atlas_snapshot"),
            ),
            equity_event_command_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_equity_event_command_snapshot"),
            ),
            onchain_tradfi_perp_radar_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(
                    context,
                    "get_onchain_tradfi_perp_radar_snapshot",
                ),
            ),
            liquidity_regime_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_finance_liquidity_regime_snapshot"),
            ),
        )


@dataclass(frozen=True)
class MacroRuntimePanelDependencies:
    cpi_components_pressure_registry_snapshot: Callable[..., PanelPayload]
    cpi_release_calendar_snapshot: Callable[..., PanelPayload]
    cpi_release_command_center_snapshot: Callable[..., PanelPayload]
    energy_gasoline_shock_snapshot: Callable[..., PanelPayload]
    fed_rates_polymarket_gap_snapshot: Callable[..., PanelPayload]
    fed_reaction_growth_risk_board_snapshot: Callable[..., PanelPayload]
    food_retail_basket_snapshot: Callable[..., PanelPayload]
    goods_tariff_supply_watch_snapshot: Callable[..., PanelPayload]
    growth_demand_recession_tracker_snapshot: Callable[..., PanelPayload]
    inflation_nowcast_snapshot: Callable[..., PanelPayload]
    jin10_panel_snapshot: Callable[..., PanelPayload]
    labor_services_inflation_monitor_snapshot: Callable[..., PanelPayload]
    labor_wage_services_pressure_snapshot: Callable[..., PanelPayload]
    polymarket_macro_map_snapshot: Callable[..., PanelPayload]
    shelter_rent_oer_pressure_snapshot: Callable[..., PanelPayload]
    supply_tariff_import_watch_snapshot: Callable[..., PanelPayload]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MacroRuntimePanelDependencies:
        dependency_names = {
            "cpi_components_pressure_registry_snapshot": (
                "get_cpi_components_pressure_registry_snapshot"
            ),
            "cpi_release_calendar_snapshot": "get_cpi_release_calendar_snapshot",
            "cpi_release_command_center_snapshot": (
                "get_cpi_release_command_center_snapshot"
            ),
            "energy_gasoline_shock_snapshot": "get_energy_gasoline_shock_snapshot",
            "fed_rates_polymarket_gap_snapshot": (
                "get_fed_rates_polymarket_gap_snapshot"
            ),
            "fed_reaction_growth_risk_board_snapshot": (
                "get_fed_reaction_growth_risk_board_snapshot"
            ),
            "food_retail_basket_snapshot": "get_food_retail_basket_snapshot",
            "goods_tariff_supply_watch_snapshot": (
                "get_goods_tariff_supply_watch_snapshot"
            ),
            "growth_demand_recession_tracker_snapshot": (
                "get_growth_demand_recession_tracker_snapshot"
            ),
            "inflation_nowcast_snapshot": "get_inflation_nowcast_snapshot",
            "jin10_panel_snapshot": "get_jin10_panel_snapshot",
            "labor_services_inflation_monitor_snapshot": (
                "get_labor_services_inflation_monitor_snapshot"
            ),
            "labor_wage_services_pressure_snapshot": (
                "get_labor_wage_services_pressure_snapshot"
            ),
            "polymarket_macro_map_snapshot": "get_polymarket_macro_map_snapshot",
            "shelter_rent_oer_pressure_snapshot": (
                "get_shelter_rent_oer_pressure_snapshot"
            ),
            "supply_tariff_import_watch_snapshot": (
                "get_supply_tariff_import_watch_snapshot"
            ),
        }
        return cls(
            **{
                field_name: cast(
                    Callable[..., PanelPayload],
                    resolve_route_callable(context, dependency_name),
                )
                for field_name, dependency_name in dependency_names.items()
            }
        )


@dataclass(frozen=True)
class SportsRuntimePanelDependencies:
    nba_matchup_predictor_snapshot: Callable[..., PanelPayload]
    grid_esports_snapshot: Callable[..., PanelPayload]
    f1_panel_snapshot: Callable[..., PanelPayload]
    nba_intel_snapshot: Callable[..., PanelPayload]
    nba_scoreboard_snapshot: Callable[..., PanelPayload]
    sports_odds_snapshot: Callable[..., PanelPayload]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> SportsRuntimePanelDependencies:
        return cls(
            nba_matchup_predictor_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_nba_matchup_predictor_snapshot"),
            ),
            grid_esports_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_grid_esports_snapshot"),
            ),
            f1_panel_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_f1_panel_snapshot"),
            ),
            nba_intel_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_nba_intel_snapshot"),
            ),
            nba_scoreboard_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_nba_scoreboard_snapshot"),
            ),
            sports_odds_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_sports_odds_snapshot"),
            ),
        )


@dataclass(frozen=True)
class TechnologyRuntimePanelDependencies:
    panel_snapshot: Callable[..., PanelPayload]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> TechnologyRuntimePanelDependencies:
        return cls(
            panel_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_tech_panel_snapshot"),
            ),
        )


@dataclass(frozen=True)
class WorldRuntimePanelDependencies:
    geo_sanctions_shock_snapshot: Callable[..., PanelPayload]
    global_transport_shipping_snapshot: Callable[..., PanelPayload]
    natural_hazards_snapshot: Callable[..., PanelPayload]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> WorldRuntimePanelDependencies:
        return cls(
            geo_sanctions_shock_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_geo_sanctions_shock_snapshot"),
            ),
            global_transport_shipping_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(
                    context,
                    "get_global_transport_shipping_snapshot",
                ),
            ),
            natural_hazards_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_natural_hazards_snapshot"),
            ),
        )


@dataclass(frozen=True)
class RuntimePanelContext(Mapping[str, Any]):
    """Typed dependencies for runtime panels during the module migration."""

    source: Mapping[str, Any] = field(repr=False)
    commodity_symbols: Sequence[MarketSymbol]
    crypto_symbols: Sequence[MarketSymbol]
    get_market_group_snapshot: Callable[..., PanelPayload]
    get_breaking_event_radar_snapshot: Callable[..., PanelPayload]
    get_market_tv_wire_snapshot: Callable[..., PanelPayload]
    get_market_youtube_channels_snapshot: Callable[..., PanelPayload]
    get_global_weather_map_snapshot: Callable[..., PanelPayload]
    get_weather_news_snapshot: Callable[..., PanelPayload]
    finance: FinanceRuntimePanelDependencies
    macro: MacroRuntimePanelDependencies
    sports: SportsRuntimePanelDependencies
    technology: TechnologyRuntimePanelDependencies
    world: WorldRuntimePanelDependencies

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> RuntimePanelContext:
        return cls(
            source=context,
            commodity_symbols=cast(
                Sequence[MarketSymbol],
                resolve_route_value(context, "COMMODITY_SYMBOLS", ()),
            ),
            crypto_symbols=cast(
                Sequence[MarketSymbol],
                resolve_route_value(context, "CRYPTO_SYMBOLS", ()),
            ),
            get_market_group_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_market_group_snapshot"),
            ),
            get_breaking_event_radar_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_breaking_event_radar_snapshot"),
            ),
            get_market_tv_wire_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_market_tv_wire_snapshot"),
            ),
            get_market_youtube_channels_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_market_youtube_channels_snapshot"),
            ),
            get_global_weather_map_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_global_weather_map_snapshot"),
            ),
            get_weather_news_snapshot=cast(
                Callable[..., PanelPayload],
                resolve_route_callable(context, "get_weather_news_snapshot"),
            ),
            finance=FinanceRuntimePanelDependencies.from_context(context),
            macro=MacroRuntimePanelDependencies.from_context(context),
            sports=SportsRuntimePanelDependencies.from_context(context),
            technology=TechnologyRuntimePanelDependencies.from_context(context),
            world=WorldRuntimePanelDependencies.from_context(context),
        )

    def __getitem__(self, name: str) -> Any:
        return self.source[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.source)

    def __len__(self) -> int:
        return len(self.source)


@dataclass(frozen=True)
class RuntimePanelModule:
    panel_id: str
    route: str
    default_limit: int | None
    min_limit: int | None
    max_limit: int | None
    get_snapshot: Callable[..., PanelPayload]
    default_enabled: bool = True

    def clamp_limit(self, raw_value: Any = None) -> int | None:
        if self.default_limit is None:
            return None
        try:
            value = int(raw_value if raw_value is not None else self.default_limit)
        except (TypeError, ValueError):
            value = self.default_limit
        if self.min_limit is not None:
            value = max(self.min_limit, value)
        if self.max_limit is not None:
            value = min(self.max_limit, value)
        return value
