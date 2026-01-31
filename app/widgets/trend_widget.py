from __future__ import annotations
import dearpygui.dearpygui as dpg

def build(widget_id: str, ctx, parent_tag: str):
    st = ctx.state
    combo_tag = f"{widget_id}::combo"
    series_tag = f"{widget_id}::series"

    st.trend_map.setdefault(widget_id, st.global_trend_tag or "")
    st.trend_series.setdefault(widget_id, (st.__class__.max_points if False else None))  # no-op

    with dpg.child_window(parent=parent_tag, autosize_x=True, height=360, border=True):
        dpg.add_text(f"Trend Widget: {widget_id}")

        with dpg.group(horizontal=True):
            dpg.add_text("Tag:")
            dpg.add_combo(items=st.get_tags(), width=260, tag=combo_tag, default_value=st.trend_map.get(widget_id, ""))

            def apply():
                tag = dpg.get_value(combo_tag)
                with st.lock:
                    st.trend_map[widget_id] = tag
                    if widget_id in st.trend_series:
                        xs, ys = st.trend_series[widget_id]
                        xs.clear(); ys.clear()
                ctx.status(f"{widget_id} -> {tag}")

            dpg.add_button(label="Apply", callback=lambda: apply())
            dpg.add_button(label="Use global", callback=lambda: (dpg.set_value(combo_tag, st.global_trend_tag), apply()) if st.global_trend_tag else None)

        with dpg.plot(label="", height=260, width=-1):
            dpg.add_plot_axis(dpg.mvXAxis, label="t, sec")
            with dpg.plot_axis(dpg.mvYAxis, label="value"):
                dpg.add_line_series([], [], tag=series_tag, label="")

    return {"type": "Trend", "combo_tag": combo_tag, "series_tag": series_tag}
