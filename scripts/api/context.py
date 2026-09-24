"""Explicit runtime dependency container for the polyData API."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from flask import Flask

    from api.config import ApiSettings


@dataclass(frozen=True)
class ServiceContext(Mapping[str, Any]):
    """Own the API's shared runtime dependencies.

    ``Mapping`` compatibility is intentional while legacy services are migrated
    from string-key lookups. New boundaries should depend on the explicit
    attributes instead of creating another helper dictionary.
    """

    application: Flask
    settings: ApiSettings
    database_path: str
    content_runtime_provider: Any
    lob_runtime_manager: Any
    snapshot_store: Any
    capabilities: Mapping[str, Any] = field(repr=False)
    runtime_state: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        values = dict(self.capabilities)
        explicit_values = {
            "app": self.application,
            "SETTINGS": self.settings,
            "DB_PATH": self.database_path,
            "CONTENT_RUNTIME_PROVIDER": self.content_runtime_provider,
            "LOB_RUNTIME_MANAGER": self.lob_runtime_manager,
            "SNAPSHOT_STORE": self.snapshot_store,
        }
        for name, value in explicit_values.items():
            existing = values.get(name, value)
            if existing is not value and existing != value:
                raise ValueError(f"ServiceContext capability {name} conflicts with its explicit dependency")
            values[name] = value
        object.__setattr__(self, "capabilities", MappingProxyType(values))

    def require_capabilities(self, *names: str) -> None:
        missing = sorted(name for name in names if name not in self.capabilities)
        if missing:
            raise RuntimeError(f"ServiceContext is missing required capabilities: {', '.join(missing)}")

    def __getitem__(self, name: str) -> Any:
        if name in self.runtime_state:
            return self.runtime_state[name]
        try:
            return self.capabilities[name]
        except KeyError as exc:
            raise KeyError(f"Unknown ServiceContext capability: {name}") from exc

    def __iter__(self) -> Iterator[str]:
        return iter((*self.capabilities, *self.runtime_state))

    def __len__(self) -> int:
        return len(self.capabilities) + len(self.runtime_state)

    def __setitem__(self, name: str, value: Any) -> None:
        if name in self.capabilities:
            raise TypeError(f"ServiceContext dependency is immutable: {name}")
        if not name.startswith("_"):
            raise TypeError(f"ServiceContext runtime state must use a private name: {name}")
        self.runtime_state[name] = value

    def __delitem__(self, name: str) -> None:
        if name in self.capabilities:
            raise TypeError(f"ServiceContext dependency is immutable: {name}")
        del self.runtime_state[name]


@dataclass(frozen=True)
class RouteContext(Mapping[str, Any]):
    """Immutable route-facing view over a shared :class:`ServiceContext`."""

    services: ServiceContext
    capabilities: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        values = dict(self.capabilities)
        values.setdefault("app", self.services.application)
        object.__setattr__(self, "capabilities", MappingProxyType(values))

    def require_capabilities(self, *names: str) -> None:
        missing = sorted(name for name in names if name not in self.capabilities)
        if missing:
            raise RuntimeError(f"RouteContext is missing required capabilities: {', '.join(missing)}")

    def __getitem__(self, name: str) -> Any:
        try:
            return self.capabilities[name]
        except KeyError as exc:
            raise KeyError(f"Unknown RouteContext capability: {name}") from exc

    def __iter__(self) -> Iterator[str]:
        return iter(self.capabilities)

    def __len__(self) -> int:
        return len(self.capabilities)


def resolve_route_callable(context: Mapping[str, Any], name: str) -> Callable[..., Any]:
    """Resolve one typed route dependency.

    Production ``RouteContext`` instances fail during blueprint registration.
    Partial plain mappings remain useful for focused unit tests; their missing
    dependency fails only if the corresponding endpoint is exercised.
    """

    try:
        dependency = context[name]
    except KeyError as exc:
        if isinstance(context, RouteContext):
            raise RuntimeError(f"RouteContext is missing required callable: {name}") from exc

        def missing_dependency(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(f"Route dependency is unavailable: {name}")

        return missing_dependency
    if not callable(dependency):
        raise TypeError(f"Route dependency is not callable: {name}")
    return dependency


def resolve_route_value(context: Mapping[str, Any], name: str, default: Any = None) -> Any:
    """Resolve a non-callable route dependency with production fail-fast semantics."""

    try:
        return context[name]
    except KeyError as exc:
        if isinstance(context, RouteContext):
            raise RuntimeError(f"RouteContext is missing required dependency: {name}") from exc
        return default


def resolve_service_callable(context: Mapping[str, Any], name: str) -> Callable[..., Any]:
    """Resolve one service dependency with production fail-fast semantics."""

    try:
        dependency = context[name]
    except KeyError as exc:
        if isinstance(context, ServiceContext):
            raise RuntimeError(f"ServiceContext is missing required callable: {name}") from exc

        def missing_dependency(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(f"Service dependency is unavailable: {name}")

        return missing_dependency
    if not callable(dependency):
        raise TypeError(f"Service dependency is not callable: {name}")
    return dependency


def resolve_optional_service_callable(
    context: Mapping[str, Any],
    name: str,
) -> Callable[..., Any] | None:
    """Resolve an optional service callable while rejecting invalid values."""

    dependency = context.get(name)
    if dependency is None:
        return None
    if not callable(dependency):
        raise TypeError(f"Service dependency is not callable: {name}")
    return dependency


def resolve_service_value(context: Mapping[str, Any], name: str, default: Any = None) -> Any:
    """Resolve a service value with production fail-fast semantics."""

    try:
        return context[name]
    except KeyError as exc:
        if isinstance(context, ServiceContext):
            raise RuntimeError(f"ServiceContext is missing required dependency: {name}") from exc
        return default


def resolve_optional_service_value(
    context: Mapping[str, Any],
    name: str,
    default: Any = None,
) -> Any:
    """Resolve an optional service value without making it a production requirement."""

    return context.get(name, default)
