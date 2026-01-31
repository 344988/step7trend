from __future__ import annotations
import dearpygui.dearpygui as dpg

def build(widget_id: str, ctx, parent_tag: str):
    st = ctx.state
    combo_tag = f"{widget_id}::combo"
    value_tag = f"{widget_id}::value"

    st.value_map.setdefault(widget_id, st.global_trend_tag or "")

    with dpg.child_window(parent=parent_tag, autosize_x=True, height=140, border=True):
        dpg.add_text(f"Value Widget: {widget_id}")

        with dpg.group(horizontal=True):
            dpg.add_text("Tag:")
            dpg.add_combo(items=st.get_tags(), width=260, tag=combo_tag, default_value=st.value_map.get(widget_id, ""))

            def apply():
                tag = dpg.get_value(combo_tag)
                with st.lock:
                    st.value_map[widget_id] = tag
                ctx.status(f"{widget_id} -> {tag}")

            dpg.add_button(label="Apply", callback=lambda: apply())
            dpg.add_button(label="Use global", callback=lambda: (dpg.set_value(combo_tag, st.global_trend_tag), apply()) if st.global_trend_tag else None)

        dpg.add_text("Value:", bullet=True)
        dpg.add_text("-", tag=value_tag)

    return {"type": "Value", "combo_tag": combo_tag, "value_tag": value_tag}
