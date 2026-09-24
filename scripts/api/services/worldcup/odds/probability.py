from __future__ import annotations

from typing import Any, Dict, List, Optional


def safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def percent_from_price(value: Any) -> Optional[float]:
    number = safe_float(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        return number * 100
    return number


def snapshot_outcomes_from_probabilities(probabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    outcomes: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for probability in probabilities:
        if not isinstance(probability, dict):
            continue
        name = str(probability.get("outcome") or probability.get("name") or "").strip()
        implied = percent_from_price(
            probability.get("price") if probability.get("price") is not None else probability.get("impliedProbability")
        )
        if not name or implied is None or name.lower() in seen:
            continue
        seen.add(name.lower())
        outcomes.append(
            {
                "name": name,
                "impliedProbability": round(implied, 2),
                "price": probability.get("price"),
                "marketUrl": probability.get("marketUrl"),
            }
        )
    return outcomes


def ordered_outcomes(match: Dict[str, Any], outcomes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_name = {str(outcome.get("name") or "").lower(): outcome for outcome in outcomes if isinstance(outcome, dict)}
    ordered: List[Dict[str, Any]] = []
    for label in (str(match.get("homeTeam") or ""), "Draw", str(match.get("awayTeam") or "")):
        row = by_name.pop(label.lower(), None)
        if row:
            ordered.append(row)
    ordered.extend(by_name.values())
    return ordered
