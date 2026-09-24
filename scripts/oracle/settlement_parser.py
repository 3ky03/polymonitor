#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize Polymarket settlement signals into business outcomes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Sequence

SETTLEMENT_UNKNOWN = 0
SETTLEMENT_YES = 1
SETTLEMENT_NO = 2
SETTLEMENT_CANCELLED = 3

OUTCOME_UNKNOWN = "UNKNOWN"
OUTCOME_YES = "YES"
OUTCOME_NO = "NO"
OUTCOME_CANCELLED = "CANCELLED"

CODE_TO_OUTCOME = {
    SETTLEMENT_UNKNOWN: OUTCOME_UNKNOWN,
    SETTLEMENT_YES: OUTCOME_YES,
    SETTLEMENT_NO: OUTCOME_NO,
    SETTLEMENT_CANCELLED: OUTCOME_CANCELLED,
}
OUTCOME_TO_CODE = {value: key for key, value in CODE_TO_OUTCOME.items()}

PRICE_EPSILON = Decimal("0.000001")


@dataclass(frozen=True)
class SettlementResult:
    settlement_code: int = SETTLEMENT_UNKNOWN
    settlement_outcome: str = OUTCOME_UNKNOWN
    settlement_source: Optional[str] = None
    settlement_raw: Optional[str] = None
    settlement_event_id: Optional[int] = None
    settlement_event_time: Optional[Any] = None
    settlement_transaction: Optional[str] = None

    @property
    def known(self) -> bool:
        return self.settlement_code in {SETTLEMENT_YES, SETTLEMENT_NO, SETTLEMENT_CANCELLED}

    def with_event(self, row: Dict[str, Any]) -> "SettlementResult":
        event_id = row.get("id")
        try:
            event_id_int = int(event_id) if event_id is not None else None
        except (TypeError, ValueError):
            event_id_int = None
        return SettlementResult(
            settlement_code=self.settlement_code,
            settlement_outcome=self.settlement_outcome,
            settlement_source=self.settlement_source,
            settlement_raw=self.settlement_raw,
            settlement_event_id=event_id_int,
            settlement_event_time=row.get("event_time"),
            settlement_transaction=_clean_text(row.get("settlement_transaction")) or None,
        )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8", errors="replace").strip()
        except Exception:
            return str(value).strip()
    return str(value).strip()


def _decimal_from_value(value: Any) -> Optional[Decimal]:
    text = _clean_text(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if abs(number) >= Decimal("1000000000000"):
        number = number / Decimal("1000000000000000000")
    return number


def _result(code: int, source: str, raw: Any) -> SettlementResult:
    raw_text = _clean_text(raw)
    return SettlementResult(
        settlement_code=code,
        settlement_outcome=CODE_TO_OUTCOME.get(code, OUTCOME_UNKNOWN),
        settlement_source=source if code != SETTLEMENT_UNKNOWN else None,
        settlement_raw=raw_text or None,
    )


def parse_settled_price(value: Any) -> SettlementResult:
    number = _decimal_from_value(value)
    if number is None:
        return SettlementResult(settlement_raw=_clean_text(value) or None)
    if abs(number - Decimal("1")) <= PRICE_EPSILON:
        return _result(SETTLEMENT_YES, "oracle_settled_price", value)
    if abs(number - Decimal("0")) <= PRICE_EPSILON:
        return _result(SETTLEMENT_NO, "oracle_settled_price", value)
    if abs(number - Decimal("0.5")) <= PRICE_EPSILON:
        return _result(SETTLEMENT_CANCELLED, "oracle_settled_price", value)
    return SettlementResult(settlement_raw=_clean_text(value) or None)


def _parse_payout_list(value: Any) -> Optional[Sequence[Decimal]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        raw_values = value
    else:
        text = _clean_text(value)
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = [part.strip() for part in text.split(",") if part.strip()]
        raw_values = parsed if isinstance(parsed, (list, tuple)) else []
    values = []
    for raw in raw_values:
        number = _decimal_from_value(raw)
        if number is None:
            return None
        values.append(number)
    return values


def parse_payout(value: Any) -> SettlementResult:
    values = _parse_payout_list(value)
    if not values or len(values) < 2:
        return SettlementResult(settlement_raw=_clean_text(value) or None)

    yes_payout = values[0]
    no_payout = values[1]
    zero = Decimal("0")
    if yes_payout > zero and no_payout == zero:
        return _result(SETTLEMENT_YES, "oracle_payout", value)
    if yes_payout == zero and no_payout > zero:
        return _result(SETTLEMENT_NO, "oracle_payout", value)
    if yes_payout > zero and no_payout > zero and yes_payout == no_payout:
        return _result(SETTLEMENT_CANCELLED, "oracle_payout", value)
    return SettlementResult(settlement_raw=_clean_text(value) or None)


def parse_oracle_settlement_event(row: Dict[str, Any]) -> SettlementResult:
    if _clean_text(row.get("event_status")).lower() != "settle":
        return SettlementResult()
    settled_price = parse_settled_price(row.get("settled_price"))
    if settled_price.known:
        return settled_price.with_event(row)
    payout = parse_payout(row.get("payout"))
    if payout.known:
        return payout.with_event(row)
    raw = settled_price.settlement_raw or payout.settlement_raw
    return SettlementResult(settlement_raw=raw).with_event(row)


def parse_fast_settlement_code(value: Any, *, closed_time: Any = None) -> SettlementResult:
    try:
        code = int(value)
    except (TypeError, ValueError):
        code = SETTLEMENT_UNKNOWN
    if code not in CODE_TO_OUTCOME:
        code = SETTLEMENT_UNKNOWN
    if code == SETTLEMENT_UNKNOWN:
        return SettlementResult(settlement_raw=_clean_text(value) or None)
    return SettlementResult(
        settlement_code=code,
        settlement_outcome=CODE_TO_OUTCOME[code],
        settlement_source="market_resolution_fast",
        settlement_raw=_clean_text(value) or None,
        settlement_event_time=closed_time,
    )


def choose_best_settlement(
    oracle_result: Optional[SettlementResult],
    fast_result: Optional[SettlementResult],
) -> SettlementResult:
    if oracle_result and oracle_result.known:
        return oracle_result
    if fast_result and fast_result.known:
        return fast_result
    if oracle_result and oracle_result.settlement_raw:
        return oracle_result
    if fast_result and fast_result.settlement_raw:
        return fast_result
    return SettlementResult()
