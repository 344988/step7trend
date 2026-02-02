import time
import math
import random
import threading
import queue
import socket
import ctypes
import struct
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict

import tkinter as tk
from tkinter import ttk, messagebox

# ===========================
# Optional deps
# ===========================
SNAP7_OK = False
PYMODBUS_OK = False

try:
    from snap7.server import Server
    from snap7.util import set_real, set_dint
    SNAP7_OK = True
except Exception:
    SNAP7_OK = False

try:
    # pymodbus imports differ by version
    from pymodbus.server import StartTcpServer
    from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext

    # DataBlock compatibility
    try:
        from pymodbus.datastore import ModbusSparseDataBlock  # pymodbus 3.x+
        _BLOCK_KIND = "sparse"
    except Exception:
        try:
            from pymodbus.datastore.store import ModbusSparseDataBlock  # older layout
            _BLOCK_KIND = "sparse"
        except Exception:
            from pymodbus.datastore import ModbusSequentialDataBlock  # safest fallback
            ModbusSparseDataBlock = None  # sentinel
            _BLOCK_KIND = "sequential"

    PYMODBUS_OK = True
except Exception:
    PYMODBUS_OK = False
    _BLOCK_KIND = "none"

# ===========================
# Defaults / layout
# ===========================
DB_NUMBER = 1
DB_SIZE = 64
SRV_AREA_DB = 5  # snap7 srvAreaDB = 5

# DB1 offsets for PN_*
OFF_PN_TEMP = 0
OFF_PN_LEVEL = 4
OFF_PN_ENCODER = 8
OFF_PN_CURRENT = 12
OFF_PN_SPEED = 16

# Modbus Holding Registers (0-based inside pymodbus)
MB_OFF_TEMP = 0   # 2 regs float
MB_OFF_LEVEL = 2
MB_OFF_FLOW = 4
MB_OFF_PRESS = 6
MB_OFF_COUNTER = 8  # 2 regs u32

UPDATE_DT_DEFAULT = 0.05
REPORT_UI_DT_MS = 200

# ===========================
# Helpers
# ===========================
def guess_local_ip() -> str:
    # best-effort: find a non-loopback IPv4
    candidates = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None)
        for info in infos:
            ip = info[4][0]
            if "." in ip and not ip.startswith("127."):
                candidates.append(ip)
    except Exception:
        pass

    # UDP trick
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if "." in ip and not ip.startswith("127."):
            candidates.append(ip)
    except Exception:
        pass

    # unique + prefer private ranges
    uniq = []
    for ip in candidates:
        if ip not in uniq:
            uniq.append(ip)

    for pref in ("10.", "192.168.", "172.16."):
        for ip in uniq:
            if ip.startswith(pref):
                return ip

    return uniq[0] if uniq else "127.0.0.1"


def float_to_regs_be(value: float) -> Tuple[int, int]:
    b = struct.pack(">f", float(value))
    hi, lo = struct.unpack(">HH", b)
    return hi, lo


def u32_to_regs_be(value: int) -> Tuple[int, int]:
    b = struct.pack(">I", int(value) & 0xFFFFFFFF)
    hi, lo = struct.unpack(">HH", b)
    return hi, lo


def ts() -> str:
    return time.strftime("%H:%M:%S")


# ===========================
# Shared state
# ===========================
@dataclass
class TagState:
    # PN_* (S7 DB1)
    pn_temp: float = 0.0
    pn_level: float = 0.0
    pn_encoder: int = 0
    pn_current: float = 0.0
    pn_speed: float = 0.0

    # MB_* (Modbus)
    mb_temp: float = 0.0
    mb_level: float = 0.0
    mb_flow: float = 0.0
    mb_press: float = 0.0
    mb_counter: int = 0

    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class ModbusStats:
    reads_total: int = 0
    writes_total: int = 0
    last_read_ts: float = 0.0
    last_write_ts: float = 0.0
    last_read_range: Optional[Tuple[int, int]] = None   # (address, count)
    last_write_range: Optional[Tuple[int, int]] = None  # (address, count)


# ===========================
# Logging
# ===========================
class LogSink:
    def __init__(self):
        self.q = queue.Queue()

    def log(self, level: str, msg: str):
        self.q.put((time.time(), level.upper(), msg))

    def info(self, msg: str): self.log("INFO", msg)
    def warn(self, msg: str): self.log("WARN", msg)
    def err(self, msg: str): self.log("ERROR", msg)


# ===========================
# Modbus datastore with capture
# ===========================
def make_capturing_block(initial_size: int, stats: ModbusStats, logger: LogSink):
    """
    Returns a DataBlock that counts reads/writes.
    Uses SparseDataBlock when available, otherwise Sequential.
    """

    if not PYMODBUS_OK:
        return None

    if _BLOCK_KIND == "sparse":
        initial = {i: 0 for i in range(0, initial_size)}

        class CapturingSparse(ModbusSparseDataBlock):  # type: ignore
            def getValues(self, address, count=1):
                stats.reads_total += 1
                stats.last_read_ts = time.time()
                stats.last_read_range = (address, count)
                logger.info(f"Modbus READ  addr={address} count={count}")
                return super().getValues(address, count)

            def setValues(self, address, values):
                stats.writes_total += 1
                stats.last_write_ts = time.time()
                stats.last_write_range = (address, len(values))
                logger.info(f"Modbus WRITE addr={address} count={len(values)} values={values[:8]}{'...' if len(values)>8 else ''}")
                return super().setValues(address, values)

        return CapturingSparse(initial)

    # sequential fallback (works broadly)
    from pymodbus.datastore import ModbusSequentialDataBlock

    class CapturingSeq(ModbusSequentialDataBlock):
        def getValues(self, address, count=1):
            stats.reads_total += 1
            stats.last_read_ts = time.time()
            stats.last_read_range = (address, count)
            logger.info(f"Modbus READ  addr={address} count={count}")
            return super().getValues(address, count)

        def setValues(self, address, values):
            stats.writes_total += 1
            stats.last_write_ts = time.time()
            stats.last_write_range = (address, len(values))
            logger.info(f"Modbus WRITE addr={address} count={len(values)} values={values[:8]}{'...' if len(values)>8 else ''}")
            return super().setValues(address, values)

    return CapturingSeq(0, [0] * initial_size)


# ===========================
# Generator thread
# ===========================
def generator_loop(state: TagState, stop_evt: threading.Event, dt_getter, logger: LogSink):
    t0 = time.perf_counter()
    enc = 0
    cnt = 0

    logger.info("Generator started")
    while not stop_evt.is_set():
        now = time.perf_counter()
        t = now - t0

        # PN_* (S7 DB1)
        pn_temp = 20.0 + 5.0 * math.sin(t / 5.0) + random.uniform(-0.2, 0.2)
        pn_level = 50.0 + 20.0 * math.sin(t / 7.0)
        enc = (enc + 5) % 1_000_000
        pn_current = 3.0 + 0.5 * math.sin(t / 2.0) + random.uniform(-0.05, 0.05)
        pn_speed = 1500.0 + 200.0 * math.sin(t / 3.0)

        # MB_* (Modbus)
        mb_temp = 60.0 + 10.0 * math.sin(t / 4.0) + random.uniform(-0.3, 0.3)
        mb_level = 10.0 + 5.0 * (0.5 + 0.5 * math.sin(t / 6.0))
        mb_flow = 1.5 + 0.3 * math.sin(t / 1.5)
        mb_press = 2.0 + 0.2 * math.sin(t / 2.5) + random.uniform(-0.02, 0.02)
        cnt = (cnt + 1) & 0xFFFFFFFF

        with state.lock:
            state.pn_temp = float(pn_temp)
            state.pn_level = float(pn_level)
            state.pn_encoder = int(enc)
            state.pn_current = float(pn_current)
            state.pn_speed = float(pn_speed)

            state.mb_temp = float(mb_temp)
            state.mb_level = float(mb_level)
            state.mb_flow = float(mb_flow)
            state.mb_press = float(mb_press)
            state.mb_counter = int(cnt)

        time.sleep(max(0.005, float(dt_getter())))

    logger.info("Generator stopped")


# ===========================
# S7 server thread (PN_* via DB1)
# ===========================
def s7_server_loop(state: TagState, stop_evt: threading.Event, port_getter, logger: LogSink):
    if not SNAP7_OK:
        logger.err("python-snap7 is not installed. Install: pip install python-snap7")
        return

    db1 = bytearray(DB_SIZE)
    db1_ctypes = (ctypes.c_uint8 * DB_SIZE).from_buffer(db1)

    srv = Server()
    area = ctypes.c_int(SRV_AREA_DB)
    srv.register_area(area, DB_NUMBER, db1_ctypes)

    port = int(port_getter())
    started_port = None

    try:
        # Some versions accept tcpport=, some just positional
        try:
            srv.start(tcpport=port)
            started_port = port
        except TypeError:
            srv.start(port)
            started_port = port
        except Exception:
            srv.start()
            started_port = 102

        logger.info(f"S7 server started (DB1) on port {started_port} (PN_* tags)")
        logger.info("S7 DB1 layout: DBD0 TEMP, DBD4 LEVEL, DBD8 ENC(DINT), DBD12 CURR, DBD16 SPEED")

        while not stop_evt.is_set():
            with state.lock:
                set_real(db1, OFF_PN_TEMP, state.pn_temp)
                set_real(db1, OFF_PN_LEVEL, state.pn_level)
                set_dint(db1, OFF_PN_ENCODER, state.pn_encoder)
                set_real(db1, OFF_PN_CURRENT, state.pn_current)
                set_real(db1, OFF_PN_SPEED, state.pn_speed)
            time.sleep(0.01)

    except Exception as e:
        logger.err(f"S7 server error: {e}")
    finally:
        try:
            srv.stop()
        except Exception:
            pass
        logger.info("S7 server stopped")


# ===========================
# Modbus server thread (MB_* via Holding Registers)
# ===========================
def modbus_writer_loop(state: TagState, stop_evt: threading.Event, context, dt_getter):
    while not stop_evt.is_set():
        with state.lock:
            t = state.mb_temp
            l = state.mb_level
            f = state.mb_flow
            p = state.mb_press
            c = state.mb_counter

        regs: Dict[int, int] = {}
        regs[MB_OFF_TEMP], regs[MB_OFF_TEMP + 1] = float_to_regs_be(t)
        regs[MB_OFF_LEVEL], regs[MB_OFF_LEVEL + 1] = float_to_regs_be(l)
        regs[MB_OFF_FLOW], regs[MB_OFF_FLOW + 1] = float_to_regs_be(f)
        regs[MB_OFF_PRESS], regs[MB_OFF_PRESS + 1] = float_to_regs_be(p)
        regs[MB_OFF_COUNTER], regs[MB_OFF_COUNTER + 1] = u32_to_regs_be(c)

        slave = context[0x00]
        hr = slave.store["h"]
        for addr, val in regs.items():
            hr.setValues(addr, [val])

        time.sleep(max(0.005, float(dt_getter())))


def modbus_server_loop(state: TagState, stop_evt: threading.Event, host_getter, port_getter, dt_getter,
                      stats: ModbusStats, logger: LogSink):
    if not PYMODBUS_OK:
        logger.err("pymodbus is not installed. Install: pip install pymodbus")
        return

    try:
        # Capturing HR block
        hr_block = make_capturing_block(initial_size=32, stats=stats, logger=logger)

        # Minimal blocks for other spaces
        # (do not capture those; only HR matters for your tags)
        try:
            # if sparse available
            if _BLOCK_KIND == "sparse":
                from pymodbus.datastore import ModbusSparseDataBlock as _S
                empty = _S({})
                di = empty
                co = empty
                ir = empty
            else:
                from pymodbus.datastore import ModbusSequentialDataBlock as _Q
                di = _Q(0, [])
                co = _Q(0, [])
                ir = _Q(0, [])
        except Exception:
            di = co = ir = hr_block  # last resort

        store = ModbusSlaveContext(
            di=di,
            co=co,
            ir=ir,
            hr=hr_block,
            zero_mode=True,
        )
        ctx = ModbusServerContext(slaves=store, single=True)

        host = str(host_getter()).strip() or "0.0.0.0"
        port = int(port_getter())

        # Writer thread (feeds generated values into HR)
        tw = threading.Thread(target=modbus_writer_loop, args=(state, stop_evt, ctx, dt_getter), daemon=True)
        tw.start()

        logger.info(f"Modbus TCP server started on {host}:{port} (MB_* tags)")
        logger.info("Holding Registers map (1-based human):")
        logger.info("  40001-40002 MB_TEMP   float32 (BE words)")
        logger.info("  40003-40004 MB_LEVEL  float32 (BE words)")
        logger.info("  40005-40006 MB_FLOW   float32 (BE words)")
        logger.info("  40007-40008 MB_PRESS  float32 (BE words)")
        logger.info("  40009-40010 MB_COUNTER uint32 (BE words)")

        # This call blocks until process ends; we stop by setting stop_evt and letting app exit.
        # Some pymodbus versions don’t support graceful stop of StartTcpServer; typical pattern is run as daemon.
        StartTcpServer(context=ctx, address=(host, port))

    except Exception as e:
        logger.err(f"Modbus server error: {e}")
    finally:
        logger.info("Modbus server stopped (process exit)")


# ===========================
# GUI
# ===========================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Siemens Simulator GUI (PN_* via S7 DB1 + MB_* via Modbus TCP)")
        self.geometry("1050x700")

        self.state = TagState()
        self.logger = LogSink()
        self.stop_evt = threading.Event()

        self.modbus_stats = ModbusStats()

        self._threads = []
        self._running = False

        self._build_ui()
        self.after(REPORT_UI_DT_MS, self._ui_tick)

        self.logger.info(f"Local IP guess: {guess_local_ip()}")
        self.logger.info("Ready.")

    def _build_ui(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # Top controls
        top = ttk.Frame(frm)
        top.pack(fill="x")

        self.btn_start = ttk.Button(top, text="▶ Start", command=self.start_all)
        self.btn_start.pack(side="left")

        self.btn_stop = ttk.Button(top, text="■ Stop", command=self.stop_all, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))

        ttk.Label(top, text="Update dt (s):").pack(side="left", padx=(20, 6))
        self.var_dt = tk.DoubleVar(value=UPDATE_DT_DEFAULT)
        ttk.Entry(top, width=8, textvariable=self.var_dt).pack(side="left")

        # Settings row
        settings = ttk.LabelFrame(frm, text="Settings", padding=10)
        settings.pack(fill="x", pady=(10, 10))

        self.var_s7_port = tk.IntVar(value=1102)
        self.var_mb_host = tk.StringVar(value="0.0.0.0")
        self.var_mb_port = tk.IntVar(value=1502)

        ttk.Label(settings, text="S7 Port:").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, width=10, textvariable=self.var_s7_port).grid(row=0, column=1, sticky="w", padx=(6, 20))

        ttk.Label(settings, text="Modbus Host:").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, width=14, textvariable=self.var_mb_host).grid(row=0, column=3, sticky="w", padx=(6, 20))

        ttk.Label(settings, text="Modbus Port:").grid(row=0, column=4, sticky="w")
        ttk.Entry(settings, width=10, textvariable=self.var_mb_port).grid(row=0, column=5, sticky="w")

        for i in range(6):
            settings.grid_columnconfigure(i, weight=0)

        # Notebook
        nb = ttk.Notebook(frm)
        nb.pack(fill="both", expand=True)

        # Live tab
        tab_live = ttk.Frame(nb, padding=10)
        nb.add(tab_live, text="Live Tags")

        grid = ttk.Frame(tab_live)
        grid.pack(fill="x")

        # PN group
        pn = ttk.LabelFrame(grid, text="PN_* (S7 DB1)", padding=10)
        pn.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.lbl_pn_temp = ttk.Label(pn, text="PN_TEMP: -")
        self.lbl_pn_level = ttk.Label(pn, text="PN_LEVEL: -")
        self.lbl_pn_enc = ttk.Label(pn, text="PN_ENCODER: -")
        self.lbl_pn_curr = ttk.Label(pn, text="PN_CURRENT: -")
        self.lbl_pn_speed = ttk.Label(pn, text="PN_SPEED: -")

        for w in (self.lbl_pn_temp, self.lbl_pn_level, self.lbl_pn_enc, self.lbl_pn_curr, self.lbl_pn_speed):
            w.pack(anchor="w", pady=2)

        ttk.Separator(pn, orient="horizontal").pack(fill="x", pady=8)

        ip = guess_local_ip()
        self.lbl_s7_hint = ttk.Label(
            pn,
            text=f"Connect example: IP {ip} / Port {self.var_s7_port.get()} / DB1 offsets DBD0..",
            foreground="#555"
        )
        self.lbl_s7_hint.pack(anchor="w")

        # MB group
        mb = ttk.LabelFrame(grid, text="MB_* (Modbus TCP Holding Registers)", padding=10)
        mb.grid(row=0, column=1, sticky="nsew")

        self.lbl_mb_temp = ttk.Label(mb, text="MB_TEMP: -")
        self.lbl_mb_level = ttk.Label(mb, text="MB_LEVEL: -")
        self.lbl_mb_flow = ttk.Label(mb, text="MB_FLOW: -")
        self.lbl_mb_press = ttk.Label(mb, text="MB_PRESS: -")
        self.lbl_mb_cnt = ttk.Label(mb, text="MB_COUNTER: -")

        for w in (self.lbl_mb_temp, self.lbl_mb_level, self.lbl_mb_flow, self.lbl_mb_press, self.lbl_mb_cnt):
            w.pack(anchor="w", pady=2)

        ttk.Separator(mb, orient="horizontal").pack(fill="x", pady=8)

        self.lbl_mb_map = ttk.Label(
            mb,
            text="Map: 40001..40010 (BE words). Read activity below = 'перехват' OK.",
            foreground="#555"
        )
        self.lbl_mb_map.pack(anchor="w")

        self.lbl_mb_stats = ttk.Label(mb, text="Reads: 0 (last: -), Writes: 0 (last: -)")
        self.lbl_mb_stats.pack(anchor="w", pady=(8, 0))

        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        # Status tab
        tab_status = ttk.Frame(nb, padding=10)
        nb.add(tab_status, text="Status")

        self.var_status = tk.StringVar(value="Stopped")
        ttk.Label(tab_status, text="Simulator status:", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(tab_status, textvariable=self.var_status).pack(anchor="w", pady=(4, 10))

        self.var_deps = tk.StringVar(value=self._deps_text())
        ttk.Label(tab_status, text="Dependencies:", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(tab_status, textvariable=self.var_deps, justify="left").pack(anchor="w", pady=(4, 0))

        # Logs tab
        tab_logs = ttk.Frame(nb, padding=10)
        nb.add(tab_logs, text="Logs")

        self.txt = tk.Text(tab_logs, height=20, wrap="none")
        self.txt.pack(fill="both", expand=True)

        scroll_y = ttk.Scrollbar(self.txt, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")

        # Footer
        footer = ttk.Frame(frm)
        footer.pack(fill="x", pady=(8, 0))
        self.lbl_footer = ttk.Label(footer, text="Tip: Modbus reads in logs = you successfully intercepted MB_*.", foreground="#555")
        self.lbl_footer.pack(side="left")

    def _deps_text(self) -> str:
        lines = []
        lines.append(f"python-snap7: {'OK' if SNAP7_OK else 'NOT INSTALLED'}")
        lines.append(f"pymodbus:     {'OK' if PYMODBUS_OK else 'NOT INSTALLED'} (block={_BLOCK_KIND})")
        lines.append("")
        lines.append("Install:")
        lines.append("  pip install python-snap7 pymodbus")
        return "\n".join(lines)

    # Getters for threads
    def _get_dt(self): return float(self.var_dt.get())
    def _get_s7_port(self): return int(self.var_s7_port.get())
    def _get_mb_host(self): return str(self.var_mb_host.get())
    def _get_mb_port(self): return int(self.var_mb_port.get())

    def start_all(self):
        if self._running:
            return

        if not SNAP7_OK and not PYMODBUS_OK:
            messagebox.showerror("Missing dependencies", "Neither python-snap7 nor pymodbus is installed.\nInstall:\n  pip install python-snap7 pymodbus")
            return

        self.stop_evt.clear()
        self._threads.clear()

        # Generator
        tg = threading.Thread(target=generator_loop, args=(self.state, self.stop_evt, self._get_dt, self.logger), daemon=True)
        tg.start()
        self._threads.append(tg)

        # S7
        if SNAP7_OK:
            ts7 = threading.Thread(
                target=s7_server_loop,
                args=(self.state, self.stop_evt, self._get_s7_port, self.logger),
                daemon=True
            )
            ts7.start()
            self._threads.append(ts7)
        else:
            self.logger.warn("S7 part disabled (python-snap7 not installed)")

        # Modbus
        if PYMODBUS_OK:
            tmb = threading.Thread(
                target=modbus_server_loop,
                args=(self.state, self.stop_evt, self._get_mb_host, self._get_mb_port, self._get_dt, self.modbus_stats, self.logger),
                daemon=True
            )
            tmb.start()
            self._threads.append(tmb)
        else:
            self.logger.warn("Modbus part disabled (pymodbus not installed)")

        self._running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.var_status.set("Running")

        ip = guess_local_ip()
        self.lbl_s7_hint.configure(text=f"Connect example: IP {ip} / Port {self.var_s7_port.get()} / DB1 offsets DBD0..")

    def stop_all(self):
        if not self._running:
            return
        self.logger.warn("Stop requested. Some servers may stop only when process exits (pymodbus StartTcpServer).")
        self.stop_evt.set()
        self._running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Stopped (generator stopped; servers may require process exit)")
        # Note: StartTcpServer is blocking; if you need true stop/start without exiting,
        # tell me your pymodbus version — I’ll switch to async server with proper shutdown.

    def _ui_tick(self):
        # Update live values
        with self.state.lock:
            pn_temp = self.state.pn_temp
            pn_level = self.state.pn_level
            pn_enc = self.state.pn_encoder
            pn_curr = self.state.pn_current
            pn_speed = self.state.pn_speed

            mb_temp = self.state.mb_temp
            mb_level = self.state.mb_level
            mb_flow = self.state.mb_flow
            mb_press = self.state.mb_press
            mb_cnt = self.state.mb_counter

        self.lbl_pn_temp.configure(text=f"PN_TEMP:    {pn_temp:8.2f}")
        self.lbl_pn_level.configure(text=f"PN_LEVEL:   {pn_level:8.2f}")
        self.lbl_pn_enc.configure(text=f"PN_ENCODER: {pn_enc:8d}")
        self.lbl_pn_curr.configure(text=f"PN_CURRENT: {pn_curr:8.2f}")
        self.lbl_pn_speed.configure(text=f"PN_SPEED:   {pn_speed:8.1f}")

        self.lbl_mb_temp.configure(text=f"MB_TEMP:    {mb_temp:8.2f}")
        self.lbl_mb_level.configure(text=f"MB_LEVEL:   {mb_level:8.2f}")
        self.lbl_mb_flow.configure(text=f"MB_FLOW:    {mb_flow:8.2f}")
        self.lbl_mb_press.configure(text=f"MB_PRESS:   {mb_press:8.2f}")
        self.lbl_mb_cnt.configure(text=f"MB_COUNTER: {mb_cnt:8d}")

        # Modbus stats
        st = self.modbus_stats
        last_r = "-" if st.last_read_ts == 0 else time.strftime("%H:%M:%S", time.localtime(st.last_read_ts))
        last_w = "-" if st.last_write_ts == 0 else time.strftime("%H:%M:%S", time.localtime(st.last_write_ts))
        rr = "-" if not st.last_read_range else f"{st.last_read_range}"
        wr = "-" if not st.last_write_range else f"{st.last_write_range}"
        self.lbl_mb_stats.configure(
            text=f"Reads: {st.reads_total} (last: {last_r}, range: {rr}) | Writes: {st.writes_total} (last: {last_w}, range: {wr})"
        )

        # Drain logs
        drained = 0
        while True:
            try:
                tstamp, level, msg = self.logger.q.get_nowait()
            except queue.Empty:
                break
            drained += 1
            line = f"[{time.strftime('%H:%M:%S', time.localtime(tstamp))}] {level:<5} {msg}\n"
            self.txt.insert("end", line)
            self.txt.see("end")

        self.after(REPORT_UI_DT_MS, self._ui_tick)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
