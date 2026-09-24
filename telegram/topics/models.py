from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class MessageCandidate:
    topic: str
    dedupe_key: str
    text: str
    priority: str = "normal"
    metadata: Dict[str, Any] = field(default_factory=dict)
    link_preview: bool = False
    reply_markup: Dict[str, Any] | None = None

    def targets(self) -> Tuple[str, ...]:
        extra = ("monitor",) if self.priority == "high" and self.topic != "monitor" else ()
        return (self.topic, *extra)


@dataclass(frozen=True)
class FormatOutcome:
    panel_id: str
    mode: str
    candidates: Tuple[MessageCandidate, ...] = ()
    reason: str = ""

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)
