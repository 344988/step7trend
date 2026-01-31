from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Union


@dataclass
class HostHit:
    ip: str
    open_ports: List[int]


def _tcp_check(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _parse_targets(target: str) -> List[ipaddress._BaseAddress]:
    """
    Принимает:
      - CIDR: "10.10.101.0/24"
      - IP:   "10.92.44.222"
    Возвращает список ipaddress объектов хостов.
    """
    s = (target or "").strip()
    if not s:
        raise ValueError("Empty target")

    if "/" in s:
        net = ipaddress.ip_network(s, strict=False)
        return list(net.hosts())

    ip = ipaddress.ip_address(s)
    return [ip]


def scan_sync(
    target: str,
    ports: List[int],
    timeout: float = 0.25,
    workers: int = 256,
    limit_hosts: Optional[int] = None,
) -> List[HostHit]:
    """
    TCP connect scan.
    - target: CIDR (10.10.101.0/24) или одиночный IP (10.92.44.222)
    - ports: список портов для проверки
    Возвращает список HostHit, где открыт хотя бы один порт.
    """
    if not ports:
        raise ValueError("ports list is empty")

    hosts = _parse_targets(target)

    if limit_hosts is not None:
        limit_hosts = int(limit_hosts)
        if limit_hosts < 0:
            limit_hosts = 0
        hosts = hosts[:limit_hosts]

    workers = int(workers)
    if workers < 1:
        workers = 1
    if workers > 1024:
        workers = 1024

    timeout = float(timeout)
    if timeout <= 0:
        timeout = 0.25

    ports_int = [int(p) for p in ports]

    def check_host(ip_obj: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> Optional[HostHit]:
        ip = str(ip_obj)
        open_ports: List[int] = []
        for p in ports_int:
            if _tcp_check(ip, p, timeout):
                open_ports.append(p)
        if open_ports:
            return HostHit(ip=ip, open_ports=open_ports)
        return None

    hits: List[HostHit] = []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(check_host, h) for h in hosts]
        for f in as_completed(futures):
            r = f.result()
            if r is not None:
                hits.append(r)

    hits.sort(key=lambda x: ipaddress.ip_address(x.ip))
    return hits
