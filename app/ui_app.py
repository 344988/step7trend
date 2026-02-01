from __future__ import annotations

import csv
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import List

import dearpygui.dearpygui as dpg

from app.config import APP_TITLE, VIEWPORT_W, VIEWPORT_H, FONT_SIZE, DB_PATH, S7_POLL_INTERVAL
from app.state import AppState
from app.core.logger import UILogger
from app.services.scan_service import ScanService
from app.services.s7_service import S7Service
from app.storage.workspace import WorkspaceStorage
from app.drivers.s7_driver import TagSpec, SNAP7_AVAILABLE


# ---------------------------
# Global objects
# ---------------------------
state = AppState()
log = UILogger()

scan_service = ScanService()
PORTS_TO_SCAN = [102, 4840, 1102]

storage = WorkspaceStorage(DB_PATH)
s7_service = S7Service(storage=storage, poll_interval=S7_POLL_INTERVAL, state=state, logger=log.log)

# UI thread task queue (because dpg.invoke may not exist)
UI_QUEUE = deque()

TAG_LIST_PARENT = "tags_list"
TREND_SERIES_TAG = "trend_series"
TREND_TAG_COMBO = "trend_tag_combo"
TREND_MODE_TAG = "trend_mode"
TREND_WINDOW_TAG = "trend_window"
TREND_PAUSE_TAG = "trend_pause"

trend_last_update = 0.0
trend_windows = {}
trend_window_counter = 0


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
    _refresh_trend()
    _refresh_trend_windows()
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

        def on_select(sender, app_data, u=ip):
            if dpg.does_item_exist("selected_ip"):
                dpg.set_value("selected_ip", u)
            else:
                log.log("UI warning: selected_ip field not found.")
            log.set_status(f"Выбран: {u}")
            log.log(f"Selected IP: {u}")

        dpg.add_selectable(
            label=label,
            parent="scan_results",
            tag=tag,
            user_data=ip,
            callback=on_select,
        )

        with dpg.popup(tag, mousebutton=dpg.mvMouseButton_Right):
            dpg.add_menu_item(
                label="Подключиться",
                callback=lambda s, a, u=ip: connect_controller(u),
            )
            dpg.add_menu_item(
                label="Выгрузить данные",
                callback=lambda s, a, u=ip: load_tags_from_controller(u),
            )
            dpg.add_menu_item(
                label="Отключиться",
                callback=lambda s, a, u=ip: disconnect_controller(u),
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


def connect_controller(ip: str):
    try:
        if not SNAP7_AVAILABLE:
            log.set_status("python-snap7 не установлен. Установите: pip install python-snap7")
            log.log("Connect error: python-snap7 is not installed. Install it to enable S7 communication.")
            return
        s7_service.connect(ip=ip)
        state.selected_ip = ip
        log.set_status(f"Подключено: {ip}")
    except Exception as exc:
        hint = "Проверьте параметры rack/slot (обычно 0/1 или 0/2) и доступность порта 102."
        log.set_status(f"Ошибка подключения: {exc}. {hint}")
        log.log(f"Connect error: {exc} | {hint}")


def disconnect_controller(ip: str):
    try:
        s7_service.disconnect()
        log.set_status(f"Отключено: {ip}")
    except Exception as exc:
        log.set_status(f"Ошибка отключения: {exc}")
        log.log(f"Disconnect error: {exc}")


def load_tags_from_controller(ip: str):
    # S7 не дает список символов по S7comm без проекта; предлагаем импорт вручную.
    state.selected_ip = ip
    show_tag_import_dialog()


def show_tag_import_dialog():
    tag = "import_tags_dialog"
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag)

    with dpg.window(label="Импорт тегов", modal=True, width=760, height=520, tag=tag):
        dpg.add_text("Введите список тегов (CSV: name,area,db,byte_index,data_type,bit_index)")
        dpg.add_input_text(tag="tags_import_text", multiline=True, height=360, width=-1)

        with dpg.group(horizontal=True):
            dpg.add_button(label="Импорт", callback=lambda: import_tags_from_text())
            dpg.add_button(label="Закрыть", callback=lambda: dpg.delete_item(tag))


def import_tags_from_text():
    text = dpg.get_value("tags_import_text") or ""
    tags = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        name = parts[0]
        area = parts[1]
        db = int(parts[2])
        byte_index = int(parts[3])
        data_type = parts[4]
        bit_index = int(parts[5]) if len(parts) > 5 and parts[5] else None
        tags.append(
            TagSpec(
                name=name,
                area=area,
                db=db,
                byte_index=byte_index,
                data_type=data_type,
                bit_index=bit_index,
            )
        )
    if tags:
        s7_service.set_tags(tags)
        state.refresh_tags(storage.list_tags())
        _render_tags()
        log.set_status(f"Импортировано тегов: {len(tags)}")
    dpg.delete_item("import_tags_dialog")


def _render_tags():
    if not dpg.does_item_exist(TAG_LIST_PARENT):
        return
    dpg.delete_item(TAG_LIST_PARENT, children_only=True)
    tags = storage.list_tags()
    if not tags:
        dpg.add_text("Теги не загружены.", parent=TAG_LIST_PARENT)
        return
    if dpg.does_item_exist(TREND_TAG_COMBO):
        dpg.configure_item(TREND_TAG_COMBO, items=tags)
    for window in trend_windows.values():
        if dpg.does_item_exist(window["tag"]):
            dpg.configure_item(window["tag"], items=tags)
    for tag in tags:
        with dpg.group(horizontal=True, parent=TAG_LIST_PARENT):
            dpg.add_text(tag)
            dpg.add_button(label="Добавить в монитор", callback=lambda s, a, t=tag: add_monitor_tag(t))
            dpg.add_button(label="Удалить слежение", callback=lambda s, a, t=tag: remove_monitor_tag(t))
            dpg.add_button(label="Построить тренд", callback=lambda s, a, t=tag: set_trend_tag(t))


def add_monitor_tag(tag: str):
    state.monitored_tags.add(tag)
    s7_service.set_active_tags(state.monitored_tags)
    s7_service.start_polling()
    log.set_status(f"Мониторинг: {tag}")


def remove_monitor_tag(tag: str):
    if tag in state.monitored_tags:
        state.monitored_tags.remove(tag)
    s7_service.set_active_tags(state.monitored_tags)
    if not state.monitored_tags:
        s7_service.stop_polling()
    log.set_status(f"Слежение удалено: {tag}")


def set_trend_tag(tag: str):
    dpg.set_value(TREND_TAG_COMBO, tag)
    log.set_status(f"Тренд для тега: {tag}")


def _refresh_trend():
    global trend_last_update
    if not dpg.does_item_exist(TREND_SERIES_TAG):
        return
    if not dpg.does_item_exist(TREND_PAUSE_TAG):
        return
    paused = dpg.get_value(TREND_PAUSE_TAG)
    if paused:
        return
    now = time.time()
    if now - trend_last_update < 1.0:
        return
    trend_last_update = now
    tag = dpg.get_value(TREND_TAG_COMBO)
    if not tag:
        return
    window_sec = float(dpg.get_value(TREND_WINDOW_TAG))
    since_ts = now - window_sec
    points = storage.get_series(tag_name=tag, since_ts=since_ts, limit=1000)
    xs = [p[0] - since_ts for p in points]
    ys = [p[1] for p in points]
    dpg.set_value(TREND_SERIES_TAG, [xs, ys])


def _refresh_trend_windows():
    now = time.time()
    for window_id, window in list(trend_windows.items()):
        if not dpg.does_item_exist(window["series"]):
            trend_windows.pop(window_id, None)
            continue
        if dpg.get_value(window["pause"]):
            continue
        last = window.get("last_update", 0.0)
        if now - last < 1.0:
            continue
        window["last_update"] = now
        tag = dpg.get_value(window["tag"])
        if not tag:
            continue
        window_sec = float(dpg.get_value(window["window"]))
        since_ts = now - window_sec
        points = storage.get_series(tag_name=tag, since_ts=since_ts, limit=1000)
        xs = [p[0] - since_ts for p in points]
        ys = [p[1] for p in points]
        dpg.set_value(window["series"], [xs, ys])
        y_min = float(dpg.get_value(window["y_min"]))
        y_max = float(dpg.get_value(window["y_max"]))
        if y_max > y_min:
            dpg.set_axis_limits(window["y_axis"], y_min, y_max)


def open_trend_window():
    global trend_window_counter
    trend_window_counter += 1
    window_id = f"trend_window_{trend_window_counter}"
    tag_combo = f"{window_id}_tag"
    window_input = f"{window_id}_window"
    pause_checkbox = f"{window_id}_pause"
    y_min = f"{window_id}_y_min"
    y_max = f"{window_id}_y_max"
    series_tag = f"{window_id}_series"
    y_axis = f"{window_id}_y_axis"

    with dpg.window(label=f"Тренд #{trend_window_counter}", width=520, height=360, tag=window_id):
        with dpg.group(horizontal=True):
            dpg.add_combo(items=state.get_tags(), width=200, tag=tag_combo, default_value="")
            dpg.add_input_float(label="Окно, сек", default_value=300.0, width=140, tag=window_input)
            dpg.add_checkbox(label="Пауза", default_value=False, tag=pause_checkbox)
        with dpg.group(horizontal=True):
            dpg.add_slider_float(label="Мин", min_value=-1000.0, max_value=1000.0, default_value=0.0, width=220, tag=y_min)
            dpg.add_slider_float(label="Макс", min_value=-1000.0, max_value=1000.0, default_value=100.0, width=220, tag=y_max)
        with dpg.plot(label="", height=220, width=-1):
            dpg.add_plot_axis(dpg.mvXAxis, label="t, sec")
            with dpg.plot_axis(dpg.mvYAxis, label="value", tag=y_axis):
                dpg.add_line_series([], [], tag=series_tag, label="")

    trend_windows[window_id] = {
        "tag": tag_combo,
        "window": window_input,
        "pause": pause_checkbox,
        "y_min": y_min,
        "y_max": y_max,
        "series": series_tag,
        "y_axis": y_axis,
        "last_update": 0.0,
    }


def export_to_excel():
    tag = dpg.get_value(TREND_TAG_COMBO)
    if not tag:
        log.set_status("Выберите тег для экспорта")
        return
    start_txt = dpg.get_value("export_start")
    end_txt = dpg.get_value("export_end")
    step = float(dpg.get_value("export_step"))

    def parse_dt(value: str) -> float:
        if not value:
            return time.time()
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return float(value)

    start_ts = parse_dt(start_txt)
    end_ts = parse_dt(end_txt)
    if end_ts <= start_ts:
        log.set_status("Диапазон времени некорректен")
        return

    rows = storage.get_series(tag_name=tag, since_ts=start_ts, limit=20000)
    filtered = []
    last_ts = 0.0
    for ts, value in rows:
        if ts > end_ts:
            continue
        if not filtered or ts - last_ts >= step:
            filtered.append((ts, value))
            last_ts = ts

    filename = f"{tag}_export.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "datetime", "value"])
        for ts, value in filtered:
            writer.writerow([ts, datetime.fromtimestamp(ts).isoformat(), value])
    log.set_status(f"Экспортировано: {filename}")


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

        dpg.add_input_text(label="Selected IP", default_value="", width=260, tag="selected_ip", readonly=True)

        with dpg.child_window(tag="scan_results", height=220, autosize_x=True, border=True):
            dpg.add_text("Результаты появятся здесь.")

        dpg.add_separator()
        dpg.add_text("Теги:")
        with dpg.child_window(tag=TAG_LIST_PARENT, height=180, autosize_x=True, border=True):
            dpg.add_text("Теги не загружены.")

        dpg.add_separator()
        dpg.add_text("Тренд (онлайн/оффлайн):")
        with dpg.group(horizontal=True):
            dpg.add_combo(items=state.get_tags(), width=260, tag=TREND_TAG_COMBO, default_value="")
            dpg.add_input_float(label="Окно, сек", default_value=300.0, width=140, tag=TREND_WINDOW_TAG)
            dpg.add_checkbox(label="Пауза", default_value=False, tag=TREND_PAUSE_TAG)
            dpg.add_button(label="Play", callback=lambda: dpg.set_value(TREND_PAUSE_TAG, False))
            dpg.add_button(label="Pause", callback=lambda: dpg.set_value(TREND_PAUSE_TAG, True))
            dpg.add_button(label="Новое окно тренда", callback=lambda: open_trend_window())

        with dpg.plot(label="", height=260, width=-1):
            dpg.add_plot_axis(dpg.mvXAxis, label="t, sec")
            with dpg.plot_axis(dpg.mvYAxis, label="value"):
                dpg.add_line_series([], [], tag=TREND_SERIES_TAG, label="")

        dpg.add_separator()
        dpg.add_text("Экспорт в Excel (CSV):")
        with dpg.group(horizontal=True):
            dpg.add_input_text(label="Start (ISO/ts)", default_value="", width=220, tag="export_start")
            dpg.add_input_text(label="End (ISO/ts)", default_value="", width=220, tag="export_end")
            dpg.add_input_float(label="Step сек", default_value=1.0, width=120, tag="export_step")
            dpg.add_button(label="Сделать выборку", callback=lambda: export_to_excel())

        dpg.add_separator()
        dpg.add_text("Debug Console:")
        log.build_console(height=260)


def run():
    dpg.create_context()
    _setup_font()

    _build_layout()
    _render_tags()

    dpg.create_viewport(title=APP_TITLE, width=VIEWPORT_W, height=VIEWPORT_H)
    dpg.setup_dearpygui()
    dpg.show_viewport()

    # Start UI queue pump
    dpg.set_frame_callback(dpg.get_frame_count() + 1, _frame_cb)

    log.log("UI started")
    dpg.start_dearpygui()
    dpg.destroy_context()
