from __future__ import annotations

import datetime
import traceback
from collections import deque
from typing import Callable, Optional

import dearpygui.dearpygui as dpg


class UILogger:
    def __init__(self, max_lines: int = 2000):
        self.buf = deque(maxlen=max_lines)
        self.console_text_tag = "dbg_console_text"
        self.console_child_tag = "dbg_console_child"
        self.status_tag = "status"

    def log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        self.buf.append(line)
        print(line)

        if dpg.does_item_exist(self.console_text_tag):
            dpg.set_value(self.console_text_tag, "\n".join(self.buf))
            try:
                dpg.set_y_scroll(self.console_child_tag, 10**9)
            except Exception:
                pass

    def set_status(self, msg: str):
        if dpg.does_item_exist(self.status_tag):
            dpg.set_value(self.status_tag, msg)

    def safe_cb(self, name: str, fn: Callable):
        def _wrap(sender=None, app_data=None, user_data=None):
            self.log(f"ACTION: {name}")
            try:
                return fn(sender, app_data, user_data)
            except TypeError:
                try:
                    return fn()
                except Exception as e:
                    self.log(f"ERROR in {name}: {e}")
                    self.log(traceback.format_exc())
                    self.set_status(f"ERROR: {e}")
            except Exception as e:
                self.log(f"ERROR in {name}: {e}")
                self.log(traceback.format_exc())
                self.set_status(f"ERROR: {e}")
        return _wrap

    def build_console(self, parent: Optional[str] = None, height: int = 220):
        group_kwargs = {"horizontal": True}
        if parent is not None:
            group_kwargs["parent"] = parent

        with dpg.group(**group_kwargs):
            dpg.add_button(
                label="Clear",
                callback=lambda: (self.buf.clear(), dpg.set_value(self.console_text_tag, "")),
            )
            dpg.add_button(
                label="Copy",
                callback=lambda: dpg.set_clipboard_text("\n".join(self.buf)),
            )

        child_kwargs = {
            "tag": self.console_child_tag,
            "height": int(height),
            "autosize_x": True,
            "border": True,
        }
        if parent is not None:
            child_kwargs["parent"] = parent

        with dpg.child_window(**child_kwargs):
            dpg.add_input_text(
                tag=self.console_text_tag,
                multiline=True,
                readonly=True,
                width=-1,
                height=-1,
                default_value="",
            )

