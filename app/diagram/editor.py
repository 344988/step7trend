from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional

import dearpygui.dearpygui as dpg


@dataclass
class DeviceNode:
    id: str
    name: str
    ip: str
    proto: str  # "S7" / "OPCUA"


class DiagramEditor:
    """
    Node Editor для "рисования" схемы:
    - Add device (S7 / OPCUA)
    - Links (IN/OUT)
    - Save/Load JSON
    """

    def __init__(self, tag_prefix: str = "diagram"):
        self.tag_prefix = tag_prefix
        self.node_editor_tag = f"{tag_prefix}::node_editor"

        self.nodes: Dict[str, DeviceNode] = {}
        self.links: List[Tuple[str, str]] = []  # (out_attr_tag, in_attr_tag)

    # ---------- UI build ----------
    def build(self, parent: Optional[str] = None, height: int = 360):
        kwargs = dict(
            tag=self.node_editor_tag,
            callback=self._link_callback,
            delink_callback=self._delink_callback,
            minimap=True,
            height=height,
        )
        if parent:
            kwargs["parent"] = parent

        with dpg.node_editor(**kwargs):
            pass

    # ---------- Actions ----------
    def clear(self):
        self.nodes.clear()
        self.links.clear()
        if dpg.does_item_exist(self.node_editor_tag):
            dpg.delete_item(self.node_editor_tag, children_only=True)

    def add_device(self, proto: str):
        nid = f"dev_{len(self.nodes) + 1}"
        node = DeviceNode(
            id=nid,
            name=f"{proto} Device {len(self.nodes) + 1}",
            ip="",
            proto=proto,
        )
        self.nodes[nid] = node
        self._render_node(node)

    # ---------- Render node ----------
    def _render_node(self, node: DeviceNode):
        node_tag = f"{self.tag_prefix}::node::{node.id}"
        in_attr = f"{node_tag}::in"
        out_attr = f"{node_tag}::out"
        name_tag = f"{node_tag}::name"
        ip_tag = f"{node_tag}::ip"

        with dpg.node(tag=node_tag, parent=self.node_editor_tag, label=node.name):
            with dpg.node_attribute(tag=in_attr, attribute_type=dpg.mvNode_Attr_Input):
                dpg.add_text("IN")

            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                dpg.add_input_text(
                    label="Name",
                    default_value=node.name,
                    tag=name_tag,
                    width=200,
                    callback=lambda s, a, u=node.id: self._update_name(u),
                )
                dpg.add_input_text(
                    label="IP",
                    default_value=node.ip,
                    tag=ip_tag,
                    width=200,
                    callback=lambda s, a, u=node.id: self._update_ip(u),
                )
                dpg.add_text(f"Proto: {node.proto}")

            with dpg.node_attribute(tag=out_attr, attribute_type=dpg.mvNode_Attr_Output):
                dpg.add_text("OUT")

    def _update_name(self, node_id: str):
        node = self.nodes[node_id]
        node_tag = f"{self.tag_prefix}::node::{node.id}"
        name_tag = f"{node_tag}::name"
        node.name = dpg.get_value(name_tag)
        dpg.configure_item(node_tag, label=node.name)

    def _update_ip(self, node_id: str):
        node = self.nodes[node_id]
        node_tag = f"{self.tag_prefix}::node::{node.id}"
        ip_tag = f"{node_tag}::ip"
        node.ip = dpg.get_value(ip_tag)

    # ---------- Linking callbacks ----------
    def _link_callback(self, sender, app_data):
        # app_data = (attr_out, attr_in)
        attr_out, attr_in = app_data
        dpg.add_node_link(attr_out, attr_in, parent=sender)
        self.links.append((attr_out, attr_in))

    def _delink_callback(self, sender, app_data):
        # app_data = link_id
        link_id = app_data
        # best-effort remove from list
        try:
            conf = dpg.get_item_configuration(link_id)
            out_attr = conf.get("attr_1")
            in_attr = conf.get("attr_2")
            self.links = [x for x in self.links if x != (out_attr, in_attr)]
        except Exception:
            pass
        dpg.delete_item(link_id)

    # ---------- Save/Load ----------
    def export_json(self) -> str:
        payload = {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "links": [{"out": o, "in": i} for (o, i) in self.links],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def import_json(self, text: str):
        payload = json.loads(text)
        self.clear()

        for n in payload.get("nodes", []):
            node = DeviceNode(**n)
            self.nodes[node.id] = node
            self._render_node(node)

        for l in payload.get("links", []):
            out_attr = l["out"]
            in_attr = l["in"]
            try:
                dpg.add_node_link(out_attr, in_attr, parent=self.node_editor_tag)
                self.links.append((out_attr, in_attr))
            except Exception:
                pass

    def show_save_dialog(self):
        tag = f"{self.tag_prefix}::save_win"
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)

        with dpg.window(label="Save Diagram JSON", modal=True, width=760, height=560, tag=tag):
            dpg.add_input_text(multiline=True, readonly=True, height=460, width=-1, default_value=self.export_json())
            dpg.add_button(label="Close", callback=lambda: dpg.delete_item(tag))

    def show_load_dialog(self):
        win_tag = f"{self.tag_prefix}::load_win"
        txt_tag = f"{self.tag_prefix}::load_text"

        if dpg.does_item_exist(win_tag):
            dpg.delete_item(win_tag)

        with dpg.window(label="Load Diagram JSON", modal=True, width=760, height=560, tag=win_tag):
            dpg.add_text("Вставьте JSON и нажмите Load")
            dpg.add_input_text(multiline=True, height=420, width=-1, tag=txt_tag, default_value="")

            def do_load():
                txt = dpg.get_value(txt_tag)
                self.import_json(txt)
                dpg.delete_item(win_tag)

            with dpg.group(horizontal=True):
                dpg.add_button(label="Load", callback=lambda: do_load())
                dpg.add_button(label="Cancel", callback=lambda: dpg.delete_item(win_tag))
