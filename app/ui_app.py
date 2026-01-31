from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

import dearpygui.dearpygui as dpg

from app.config import APP_TITLE, VIEWPORT_W, VIEWPORT_H, FONT_SIZE
from app.state import AppState
from app.core.logger import UILogger
from app.services.scan_service import ScanService


# ---------------------------
# Global objects
# ---------------------------
state = AppState()
log = UILogger()

scan_service = ScanService()
PORTS_TO_SCAN = [102, 4840, 1102]

# UI thread task queue (because dpg.invoke may not exist)
UI_QUEUE = deque()


def ui_post(fn):
    """Schedule a function to run on the main UI thread."""
    UI_QUEUE.append(fn)


def _ui_pump():
    # Run up to N tasks per frame to keep UI responsive
    for _ in range(50):
        if not UI_QUEUE:
            break
        fn = UI_QUEUE.popleft()
        try:
            fn()
        except Exception as e:
            log.log(f"UI task error: {e}")


def _frame_cb(sender=None, app_data=None):
    _ui_pump()
    # re-schedule next frame
    dpg.set_frame_callback(dpg.get_frame_count() + 1, _frame_cb)


# ---------------------------
# UI helpers
# ---------------------------
def _setup_font():
    font_path = Path(__file__).resolve().parent / "assets" / "fonts" / "segoeui.ttf"
    if not font_path.exists():
        font_path = Path(r"C:\Windows\Fonts\segoeui.ttf")
    if not font_path.exists():
        log.log("Font: not found, using default")
        return

    try:
        with dpg.font_registry():
            with dpg.font(str(font_path), FONT_SIZE) as f:
                dpg.add_font_range(0x0020, 0x00FF)
                dpg.add_font_range(0x0400, 0x052F)  # Cyrillic
        dpg.bind_font(f)
        log.log(f"Font loaded: {font_path}")
    except Exception as e:
        log.log(f"Font load failed: {e}")


def _safe_tag_from_ip(ip: str) -> str:
    return "host_" + ip.replace(".", "_").replace(":", "_")


def _render_scan_hits(hits):
    if not dpg.does_item_exist("scan_results"):
        return

    dpg.delete_item("scan_results", children_only=True)

    if not hits:
        dpg.add_text("Ничего не найдено.", parent="scan_results")
        return

    for hit in hits:
        ip = hit.ip
        label = f"{ip}  ports={hit.open_ports}"
        tag = _safe_tag_from_ip(ip)

        dpg.add_selectable(
            label=label,
            parent="scan_results",
            tag=tag,
            user_data=ip,
            callback=lambda s, a, u=ip: (
                dpg.set_value("selected_ip", u),
                log.set_status(f"Выбран: {u}"),
                log.log(f"Selected IP: {u}"),
            ),
        )


def scan_clicked():
    target = (dpg.get_value("cidr_in") or "").strip()
    if not target:
        log.set_status("Введите CIDR или IP, например 10.10.101.0/24 или 10.92.44.222")
        return

    timeout = float(dpg.get_value("scan_timeout"))
    workers = int(dpg.get_value("scan_workers"))

    log.set_status(f"Сканирую {target} ...")
    log.log(f"SCAN start target={target} ports={PORTS_TO_SCAN} timeout={timeout} workers={workers}")

    def worker():
        t0 = time.perf_counter()
        try:
            hits = scan_service.scan(cidr_or_ip=target, ports=PORTS_TO_SCAN, timeout=timeout, workers=workers)
            dt = time.perf_counter() - t0
            log.log(f"SCAN done hits={len(hits)} elapsed={dt:.2f}s")

            def apply():
                _render_scan_hits(hits)
                log.set_status(f"Скан завершён. Найдено: {len(hits)} | {dt:.2f}s")

            ui_post(apply)

        except Exception as e:
            dt = time.perf_counter() - t0
            log.log(f"SCAN ERROR elapsed={dt:.2f}s: {e}")

            def apply_err():
                log.set_status(f"SCAN ERROR: {e}")

            ui_post(apply_err)

    threading.Thread(target=worker, daemon=True).start()


def _build_layout():
    with dpg.window(label=APP_TITLE, width=1360, height=920):
        dpg.add_text("Статус:", bullet=True)
        dpg.add_text(state.status_text, tag="status")

        with dpg.group(horizontal=True):
            dpg.add_button(label="TEST click", callback=log.safe_cb("TEST", lambda: log.set_status("TEST OK")))
            dpg.add_button(label="TEST log", callback=log.safe_cb("LOG", lambda: log.log("Hello from logger")))

        dpg.add_separator()
        dpg.add_text("Scan:")

        with dpg.group(horizontal=True):
            dpg.add_input_text(label="CIDR / IP", default_value="10.10.101.0/24", width=260, tag="cidr_in")
            dpg.add_input_float(label="timeout", default_value=0.25, width=120, tag="scan_timeout")
            dpg.add_input_int(label="workers", default_value=256, width=120, tag="scan_workers")
            dpg.add_button(label="Scan", callback=log.safe_cb("Scan", lambda *_: scan_clicked()))

        with dpg.child_window(tag="scan_results", height=220, autosize_x=True, border=True):
            dpg.add_text("Результаты появятся здесь.")

        dpg.add_separator()
        dpg.add_text("Debug Console:")
        log.build_console(height=260)


def run():
    dpg.create_context()
    _setup_font()

    _build_layout()

    dpg.create_viewport(title=APP_TITLE, width=VIEWPORT_W, height=VIEWPORT_H)
    dpg.setup_dearpygui()
    dpg.show_viewport()

    # Start UI queue pump
    dpg.set_frame_callback(dpg.get_frame_count() + 1, _frame_cb)

    log.log("UI started")
    dpg.start_dearpygui()
    dpg.destroy_context()
