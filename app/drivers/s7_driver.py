from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import struct
import traceback

SNAP7_IMPORT_ERROR = None
SNAP7_IMPORT_TRACEBACK = None
SNAP7_IMPORT_HINT = None
try:
    import snap7
    from snap7 import types as snap7_types
    from snap7 import util as snap7_util

    SNAP7_AVAILABLE = True
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    snap7 = None
    snap7_types = None
    snap7_util = None
    SNAP7_AVAILABLE = False
    SNAP7_IMPORT_ERROR = f"python-snap7 не установлен. Установите: pip install python-snap7 ({exc})"
    SNAP7_IMPORT_TRACEBACK = traceback.format_exc()
except ImportError as exc:  # pragma: no cover - optional dependency
    snap7 = None
    snap7_types = None
    snap7_util = None
    SNAP7_AVAILABLE = False
    SNAP7_IMPORT_ERROR = f"python-snap7 установлен, но импорт не удался: {exc}"
    SNAP7_IMPORT_TRACEBACK = traceback.format_exc()
except Exception as exc:  # pragma: no cover - optional dependency
    snap7 = None
    snap7_types = None
    snap7_util = None
    SNAP7_AVAILABLE = False
    SNAP7_IMPORT_ERROR = f"snap7 import failed ({type(exc).__name__}): {exc}"
    SNAP7_IMPORT_TRACEBACK = traceback.format_exc()

if SNAP7_IMPORT_ERROR:
    lowered = SNAP7_IMPORT_ERROR.lower()
    if "dll load failed" in lowered or "winerror 126" in lowered or "winerror 193" in lowered:
        SNAP7_IMPORT_HINT = (
            "Похоже, не загружается нативная DLL. Проверьте: 64-bit Python ↔ 64-bit snap7, "
            "установлен Microsoft Visual C++ Redistributable, библиотека snap7.dll доступна."
        )


@dataclass(frozen=True)
class TagSpec:
    name: str
    area: str  # DB, I, Q, M
    db: int
    byte_index: int
    data_type: str  # BOOL, BYTE, WORD, DWORD, INT, DINT, REAL
    bit_index: Optional[int] = None

    def size(self) -> int:
        sizes = {
            "BOOL": 1,
            "BYTE": 1,
            "WORD": 2,
            "DWORD": 4,
            "INT": 2,
            "DINT": 4,
            "REAL": 4,
        }
        return sizes[self.data_type.upper()]


class S7Driver:
    def __init__(self, ip: str, rack: int = 0, slot: int = 1, port: int = 102):
        self.ip = ip
        self.rack = rack
        self.slot = slot
        self.port = port
        self.client = None

    def connect(self) -> None:
        if not SNAP7_AVAILABLE:
            raise RuntimeError(
                SNAP7_IMPORT_ERROR
                or "python-snap7 не установлен. Установите: pip install python-snap7"
            )
        self.client = snap7.client.Client()
        self.client.connect(self.ip, self.rack, self.slot, self.port)

    def disconnect(self) -> None:
        if self.client:
            self.client.disconnect()
            self.client = None

    def read_tag(self, tag: TagSpec):
        if not self.client:
            raise RuntimeError("S7 client is not connected")
        area = _area_to_snap7(tag.area)
        size = tag.size()
        data = self.client.read_area(area, tag.db, tag.byte_index, size)
        return _decode_value(tag, data)

    def write_tag(self, tag: TagSpec, value) -> None:
        if not self.client:
            raise RuntimeError("S7 client is not connected")
        area = _area_to_snap7(tag.area)
        size = tag.size()

        if tag.data_type.upper() == "BOOL":
            data = bytearray(self.client.read_area(area, tag.db, tag.byte_index, size))
            _set_bool(data, 0, tag.bit_index or 0, bool(value))
            self.client.write_area(area, tag.db, tag.byte_index, data)
            return

        data = _encode_value(tag, value)
        self.client.write_area(area, tag.db, tag.byte_index, data)


def _area_to_snap7(area: str) -> int:
    if not snap7_types:
        raise RuntimeError("snap7 types are not available")
    area = area.upper()
    mapping = {
        "DB": snap7_types.Areas.DB,
        "I": snap7_types.Areas.PE,
        "Q": snap7_types.Areas.PA,
        "M": snap7_types.Areas.MK,
    }
    if area not in mapping:
        raise ValueError(f"Unsupported area '{area}'")
    return mapping[area]


def _decode_value(tag: TagSpec, data: bytes):
    dtype = tag.data_type.upper()
    if dtype == "BOOL":
        return _get_bool(data, 0, tag.bit_index or 0)
    if snap7_util:
        if dtype == "BYTE":
            return data[0]
        if dtype == "WORD":
            return snap7_util.get_word(data, 0)
        if dtype == "DWORD":
            return snap7_util.get_dword(data, 0)
        if dtype == "INT":
            return snap7_util.get_int(data, 0)
        if dtype == "DINT":
            return snap7_util.get_dint(data, 0)
        if dtype == "REAL":
            return snap7_util.get_real(data, 0)

    if dtype == "BYTE":
        return data[0]
    if dtype == "WORD":
        return struct.unpack(">H", data)[0]
    if dtype == "DWORD":
        return struct.unpack(">I", data)[0]
    if dtype == "INT":
        return struct.unpack(">h", data)[0]
    if dtype == "DINT":
        return struct.unpack(">i", data)[0]
    if dtype == "REAL":
        return struct.unpack(">f", data)[0]
    raise ValueError(f"Unsupported data type '{tag.data_type}'")


def _encode_value(tag: TagSpec, value) -> bytes:
    dtype = tag.data_type.upper()
    if snap7_util:
        if dtype == "BYTE":
            return bytes([int(value)])
        if dtype == "WORD":
            buff = bytearray(2)
            snap7_util.set_word(buff, 0, int(value))
            return buff
        if dtype == "DWORD":
            buff = bytearray(4)
            snap7_util.set_dword(buff, 0, int(value))
            return buff
        if dtype == "INT":
            buff = bytearray(2)
            snap7_util.set_int(buff, 0, int(value))
            return buff
        if dtype == "DINT":
            buff = bytearray(4)
            snap7_util.set_dint(buff, 0, int(value))
            return buff
        if dtype == "REAL":
            buff = bytearray(4)
            snap7_util.set_real(buff, 0, float(value))
            return buff

    if dtype == "BYTE":
        return bytes([int(value)])
    if dtype == "WORD":
        return struct.pack(">H", int(value))
    if dtype == "DWORD":
        return struct.pack(">I", int(value))
    if dtype == "INT":
        return struct.pack(">h", int(value))
    if dtype == "DINT":
        return struct.pack(">i", int(value))
    if dtype == "REAL":
        return struct.pack(">f", float(value))
    raise ValueError(f"Unsupported data type '{tag.data_type}'")


def _get_bool(data: bytes, byte_index: int, bit_index: int) -> bool:
    return bool(data[byte_index] & (1 << bit_index))


def _set_bool(data: bytearray, byte_index: int, bit_index: int, value: bool) -> None:
    mask = 1 << bit_index
    if value:
        data[byte_index] |= mask
    else:
        data[byte_index] &= ~mask
