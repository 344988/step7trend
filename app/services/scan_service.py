from __future__ import annotations

from typing import List, Optional

from app.scanner import HostHit, scan_sync


class ScanService:
    def scan(
        self,
        cidr_or_ip: str,
        ports: List[int],
        timeout: float = 0.25,
        workers: int = 256,
        limit_hosts: Optional[int] = None,
    ) -> List[HostHit]:
        return scan_sync(
            target=cidr_or_ip,
            ports=ports,
            timeout=timeout,
            workers=workers,
            limit_hosts=limit_hosts,
        )
