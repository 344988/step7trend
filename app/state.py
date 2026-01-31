from __future__ import annotations
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List


@dataclass
class AppState:
    lock: Lock = field(default_factory=Lock)

    # UI selections
    selected_ip: str = ""
    status_text: str = "Готово"

    # tags (позже будет заполняться из PLC)
    latest_tags: Dict[str, float] = field(default_factory=dict)
    favorites: List[str] = field(default_factory=list)
