"""
Structured, per-request stage timing for the question pipeline's log report.
Replaces pipeline.py's old string-only `trace: List[str]` with real
timestamps/durations per stage, without changing any stage's own logic -
this only wraps existing call sites.
"""
import datetime
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StageEvent:
    stage: str
    started_at: str  # ISO timestamp
    duration_ms: int
    detail: Dict[str, Any] = field(default_factory=dict)


class StageTimer:
    """
    Usage: `with timer.stage("rag_retrieval") as s: ...; s.detail["tier"] = x`.
    Records wall-clock duration around the `with` block and whatever the
    caller stashed on `s.detail` - it never touches the stage's own return
    value or behavior.
    """

    def __init__(self):
        self.events: List[StageEvent] = []
        self._run_started = time.time()

    def stage(self, name: str) -> "_StageContext":
        return _StageContext(self, name)

    def record(self, name: str, duration_ms: int, detail: Optional[Dict[str, Any]] = None) -> None:
        self.events.append(StageEvent(
            stage=name,
            started_at=datetime.datetime.now().isoformat(),
            duration_ms=duration_ms,
            detail=detail or {},
        ))

    @property
    def total_duration_ms(self) -> int:
        return round((time.time() - self._run_started) * 1000)

    def as_list(self) -> List[Dict[str, Any]]:
        return [
            {"stage": e.stage, "started_at": e.started_at, "duration_ms": e.duration_ms, **e.detail}
            for e in self.events
        ]


class _StageContext:
    def __init__(self, timer: StageTimer, name: str):
        self.timer = timer
        self.name = name
        self.detail: Dict[str, Any] = {}
        self._start: Optional[float] = None

    def __enter__(self) -> "_StageContext":
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_ms = round((time.time() - self._start) * 1000)
        if exc_type is not None:
            self.detail.setdefault("error", str(exc))
        self.timer.record(self.name, duration_ms, self.detail)
        return False
