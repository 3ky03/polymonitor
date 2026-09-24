#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helpers for keeping local, Gamma, CLOB, and token market identities separate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MarketIdentity:
    local_market_id: int
    gamma_market_id: Optional[str]
    slug: Optional[str]
    condition_id: Optional[str]
    question_id: Optional[str]
    yes_token_id: Optional[str]
    no_token_id: Optional[str]

    @classmethod
    def from_row(cls, row: Any) -> "MarketIdentity":
        data = _row_to_dict(row)
        return cls(
            local_market_id=int(data.get("id") or data.get("local_market_id") or 0),
            gamma_market_id=_clean_text(data.get("gamma_market_id")),
            slug=_clean_text(data.get("slug")),
            condition_id=_clean_text(data.get("condition_id")),
            question_id=_clean_text(data.get("question_id")),
            yes_token_id=_clean_text(data.get("yes_token_id")),
            no_token_id=_clean_text(data.get("no_token_id")),
        )

    def as_api_dict(self) -> Dict[str, Any]:
        return {
            "localMarketId": self.local_market_id,
            "gammaMarketId": self.gamma_market_id,
            "slug": self.slug,
            "conditionId": self.condition_id,
            "questionId": self.question_id,
            "yesTokenId": self.yes_token_id,
            "noTokenId": self.no_token_id,
        }


def _clean_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "as_dict"):
        return row.as_dict()
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    raise TypeError("row must be a mapping-like DB row")


def normalize_gamma_market_id(value: Any) -> Optional[str]:
    """Return a Gamma API market id string, never a local markets.id."""
    return _clean_text(value)


def normalize_condition_id(value: Any) -> Optional[str]:
    text = _clean_text(value)
    if not text:
        return None
    return text if text.startswith("0x") else "0x" + text


def gamma_market_url(gamma_api_base: str, gamma_market_id: Any, suffix: str = "") -> str:
    gamma_id = normalize_gamma_market_id(gamma_market_id)
    if not gamma_id:
        raise ValueError("gamma_market_id is required for Gamma /markets/{id} requests")
    base = str(gamma_api_base or "").rstrip("/")
    tail = str(suffix or "")
    if tail and not tail.startswith("/"):
        tail = "/" + tail
    return f"{base}/markets/{gamma_id}{tail}"


def select_market_identity_columns(alias: str = "m") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"{prefix}id, {prefix}gamma_market_id, {prefix}slug, {prefix}condition_id, "
        f"{prefix}question_id, {prefix}yes_token_id, {prefix}no_token_id"
    )


def get_market_identity_by_local_id(conn: Any, local_market_id: int) -> Optional[MarketIdentity]:
    cur = conn.execute(
        f"""
        SELECT {select_market_identity_columns("")}
        FROM markets
        WHERE id = ?
        LIMIT 1
        """,
        (int(local_market_id),),
    )
    row = cur.fetchone()
    return MarketIdentity.from_row(row) if row else None


def get_market_identity_by_slug(conn: Any, slug: Any) -> Optional[MarketIdentity]:
    slug_text = _clean_text(slug)
    if not slug_text:
        return None
    row = conn.execute(
        f"""
        SELECT {select_market_identity_columns("")}
        FROM markets
        WHERE slug = ?
        LIMIT 1
        """,
        (slug_text,),
    ).fetchone()
    return MarketIdentity.from_row(row) if row else None


def get_market_identity_by_condition_id(conn: Any, condition_id: Any) -> Optional[MarketIdentity]:
    cid = normalize_condition_id(condition_id)
    if not cid:
        return None
    row = conn.execute(
        f"""
        SELECT {select_market_identity_columns("")}
        FROM markets
        WHERE condition_id = ?
        LIMIT 1
        """,
        (cid,),
    ).fetchone()
    return MarketIdentity.from_row(row) if row else None


def get_market_identity_by_token_id(conn: Any, token_id: Any) -> Optional[MarketIdentity]:
    token = _clean_text(token_id)
    if not token:
        return None
    row = conn.execute(
        f"""
        SELECT {select_market_identity_columns("m")}
        FROM market_tokens mt
        JOIN markets m ON m.id = mt.market_id
        WHERE mt.token_id = ?
        ORDER BY
            CASE
                WHEN COALESCE(m.category, '') = 'orderfilled-placeholder'
                  OR COALESCE(m.slug, '') LIKE 'trade-indexer-placeholder-%%'
                THEN 1 ELSE 0
            END,
            CASE WHEN COALESCE(m.gamma_market_id, '') = '' THEN 1 ELSE 0 END,
            m.id DESC
        LIMIT 1
        """,
        (token,),
    ).fetchone()
    if row:
        return MarketIdentity.from_row(row)
    row = conn.execute(
        f"""
        SELECT {select_market_identity_columns("m")}
        FROM markets m
        WHERE yes_token_id = ? OR no_token_id = ?
        ORDER BY
            CASE
                WHEN COALESCE(m.category, '') = 'orderfilled-placeholder'
                  OR COALESCE(m.slug, '') LIKE 'trade-indexer-placeholder-%%'
                THEN 1 ELSE 0
            END,
            CASE WHEN COALESCE(m.gamma_market_id, '') = '' THEN 1 ELSE 0 END,
            m.id DESC
        LIMIT 1
        """,
        (token, token),
    ).fetchone()
    return MarketIdentity.from_row(row) if row else None


def resolve_market_identity(
    conn: Any,
    *,
    local_market_id: Any = None,
    gamma_market_id: Any = None,
    condition_id: Any = None,
    token_id: Any = None,
    slug: Any = None,
) -> Optional[MarketIdentity]:
    if local_market_id not in (None, ""):
        return get_market_identity_by_local_id(conn, int(local_market_id))
    if condition_id not in (None, ""):
        found = get_market_identity_by_condition_id(conn, condition_id)
        if found:
            return found
    if token_id not in (None, ""):
        found = get_market_identity_by_token_id(conn, token_id)
        if found:
            return found
    if slug not in (None, ""):
        found = get_market_identity_by_slug(conn, slug)
        if found:
            return found
    if gamma_market_id not in (None, ""):
        local_id = resolve_local_market_id_by_gamma_id(conn, gamma_market_id)
        if local_id is not None:
            return get_market_identity_by_local_id(conn, local_id)
    return None


def oracle_event_lookup_clause(identity: MarketIdentity, alias: str = "oe") -> tuple[str, tuple[Any, ...]]:
    prefix = f"{alias}." if alias else ""
    clauses = [f"{prefix}market_id = ?"]
    params: List[Any] = [identity.local_market_id]
    if identity.gamma_market_id:
        clauses.append(f"{prefix}external_market_id = ?")
        params.append(identity.gamma_market_id)
    if identity.condition_id:
        clauses.append(f"{prefix}condition_id = ?")
        params.append(identity.condition_id)
    if identity.question_id:
        clauses.append(f"{prefix}question_id = ?")
        params.append(identity.question_id)
    return " OR ".join(clauses), tuple(params)


def oracle_event_lookup_terms(identity: MarketIdentity) -> List[tuple[str, Any]]:
    terms: List[tuple[str, Any]] = [("market_id", identity.local_market_id)]
    if identity.gamma_market_id:
        terms.append(("external_market_id", identity.gamma_market_id))
    if identity.condition_id:
        terms.append(("condition_id", identity.condition_id))
    if identity.question_id:
        terms.append(("question_id", identity.question_id))
    return terms


def resolve_local_market_ids_by_gamma_id(conn: Any, gamma_market_id: Any) -> List[int]:
    gamma_id = normalize_gamma_market_id(gamma_market_id)
    if not gamma_id:
        return []
    rows = conn.execute(
        "SELECT id FROM markets WHERE gamma_market_id = ? ORDER BY id ASC",
        (gamma_id,),
    ).fetchall()
    return [int(row[0]) for row in rows]


def resolve_local_market_id_by_gamma_id(conn: Any, gamma_market_id: Any) -> Optional[int]:
    local_ids = resolve_local_market_ids_by_gamma_id(conn, gamma_market_id)
    if not local_ids:
        return None
    if len(local_ids) > 1:
        raise LookupError(
            f"gamma_market_id {gamma_market_id!r} maps to multiple local market ids: {local_ids[:5]}"
        )
    return local_ids[0]


def resolve_local_market_id_by_condition_id(conn: Any, condition_id: Any) -> Optional[int]:
    cid = normalize_condition_id(condition_id)
    if not cid:
        return None
    row = conn.execute(
        "SELECT id FROM markets WHERE condition_id = ? LIMIT 1",
        (cid,),
    ).fetchone()
    return int(row[0]) if row else None


def resolve_local_market_id_by_token_id(conn: Any, token_id: Any) -> Optional[int]:
    token = _clean_text(token_id)
    if not token:
        return None
    row = conn.execute(
        """
        SELECT market_id
        FROM market_tokens
        WHERE token_id = ?
        LIMIT 1
        """,
        (token,),
    ).fetchone()
    if row:
        return int(row[0])
    row = conn.execute(
        """
        SELECT id
        FROM markets
        WHERE yes_token_id = ? OR no_token_id = ?
        LIMIT 1
        """,
        (token, token),
    ).fetchone()
    return int(row[0]) if row else None


def require_gamma_market_id_for_local_id(conn: Any, local_market_id: int) -> str:
    identity = get_market_identity_by_local_id(conn, int(local_market_id))
    if identity is None:
        raise LookupError(f"local market id {local_market_id} does not exist")
    if not identity.gamma_market_id:
        raise LookupError(f"local market id {local_market_id} has no gamma_market_id")
    return identity.gamma_market_id
