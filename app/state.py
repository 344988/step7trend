from __future__ import annotations
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Set, Tuple


@dataclass
class AppState:
    lock: Lock = field(default_factory=Lock)

    # UI selections
    selected_ip: str = ""
    status_text: str = "Готово"

    # Tags
    latest_tags: Dict[str, float] = field(default_factory=dict)
    available_tags: List[str] = field(default_factory=list)

    # Widgets state
    global_trend_tag: str = ""
    trend_map: Dict[str, str] = field(default_factory=dict)
    trend_series: Dict[str, Tuple[List[float], List[float]]] = field(default_factory=dict)
    value_map: Dict[str, str] = field(default_factory=dict)

    monitored_tags: Set[str] = field(default_factory=set)

    @staticmethod
    def max_points() -> int:
        return 600

    def refresh_tags(self, tags: List[str]) -> None:
        with self.lock:
            self.available_tags = tags

    def get_tags(self) -> List[str]:
        with self.lock:
            return sorted(self.available_tags)
