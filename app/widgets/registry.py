from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, List

@dataclass
class WidgetContext:
    state: object
    status: Callable[[str], None]

class WidgetRegistry:
    def __init__(self):
        self._builders: Dict[str, Callable[[str, WidgetContext, str], Dict[str, str]]] = {}

    def register(self, name: str, builder):
        self._builders[name] = builder

    def names(self) -> List[str]:
        return sorted(self._builders.keys())

    def build(self, name: str, widget_id: str, ctx: WidgetContext, parent_tag: str) -> Dict[str, str]:
        return self._builders[name](widget_id, ctx, parent_tag)
