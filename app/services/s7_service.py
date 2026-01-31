from __future__ import annotations

import threading
import time
from typing import Callable, Iterable, List, Optional

from app.drivers.s7_driver import S7Driver, TagSpec
from app.storage.workspace import WorkspaceStorage
from app.state import AppState


class S7Service:
    def __init__(
        self,
        storage: WorkspaceStorage,
        tags: Optional[Iterable[TagSpec]] = None,
        poll_interval: float = 1.0,
        state: Optional[AppState] = None,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.storage = storage
        self.tags: List[TagSpec] = list(tags or [])
        self.active_tags: List[TagSpec] = list(self.tags)
        self.poll_interval = poll_interval
        self.state = state
        self.logger = logger or (lambda msg: None)

        self.driver: Optional[S7Driver] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        if self.tags:
            self.storage.upsert_tags(self.tags)

    def connect(self, ip: str, rack: int = 0, slot: int = 1, port: int = 102) -> None:
        self.driver = S7Driver(ip=ip, rack=rack, slot=slot, port=port)
        self.driver.connect()
        self.logger(f"S7 connected: {ip} rack={rack} slot={slot}")

    def disconnect(self) -> None:
        self.stop_polling()
        if self.driver:
            self.driver.disconnect()
            self.driver = None
            self.logger("S7 disconnected")

    def set_tags(self, tags: Iterable[TagSpec]) -> None:
        self.tags = list(tags)
        self.active_tags = list(self.tags)
        self.storage.upsert_tags(self.tags)
        if self.state:
            with self.state.lock:
                self.state.latest_tags = {tag.name: 0.0 for tag in self.tags}

    def set_active_tags(self, tag_names: Iterable[str]) -> None:
        selected = []
        selected_set = set(tag_names)
        for tag in self.tags:
            if tag.name in selected_set:
                selected.append(tag)
        self.active_tags = selected
        if self.state:
            with self.state.lock:
                self.state.latest_tags = {tag.name: 0.0 for tag in self.active_tags}

    def start_polling(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop_polling(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def read_once(self) -> dict:
        values = {}
        if not self.driver:
            raise RuntimeError("S7 driver is not connected")
        for tag in self.active_tags:
            values[tag.name] = self.driver.read_tag(tag)
        return values

    def write_tag(self, tag_name: str, value) -> None:
        if not self.driver:
            raise RuntimeError("S7 driver is not connected")
        tag = self._find_tag(tag_name)
        self.driver.write_tag(tag, value)
        ts = time.time()
        self.storage.insert_sample(tag.name, float(value), ts)
        if self.state:
            with self.state.lock:
                self.state.latest_tags[tag.name] = float(value)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                values = self.read_once()
                ts = time.time()
                samples = []
                for name, value in values.items():
                    samples.append((name, ts, float(value)))
                self.storage.insert_samples(samples)
                if self.state:
                    with self.state.lock:
                        self.state.latest_tags.update({k: float(v) for k, v in values.items()})
                if self.state:
                    self.state.refresh_tags(self.storage.list_tags())
            except Exception as exc:  # pragma: no cover - runtime integration
                self.logger(f"S7 poll error: {exc}")
            time.sleep(self.poll_interval)

    def _find_tag(self, tag_name: str) -> TagSpec:
        for tag in self.tags:
            if tag.name == tag_name:
                return tag
        raise KeyError(f"Tag '{tag_name}' not found")
