"""Process-safe per-egress-IP admission slots using Linux file locks."""

import asyncio
import fcntl
import hashlib
import os
from pathlib import Path
from typing import Optional, Sequence


class EgressLease:
    def __init__(self, proxy_url: Optional[str], fd: int):
        self.proxy_url = proxy_url
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


class EgressSlotPool:
    def __init__(
        self,
        proxies: Sequence[Optional[str]],
        slots_per_proxy: int = 5,
        lock_dir: str = "/run/automation-v2/egress-slots",
    ):
        self.proxies = tuple(proxies) or (None,)
        self.slots_per_proxy = slots_per_proxy
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, proxy_url: Optional[str], slot: int) -> Path:
        identity = proxy_url or "direct"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
        return self.lock_dir / f"{digest}-{slot}.lock"

    def try_acquire(self, key: str) -> Optional[EgressLease]:
        start = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(self.proxies)
        ordered = self.proxies[start:] + self.proxies[:start]
        for proxy_url in ordered:
            for slot in range(self.slots_per_proxy):
                fd = os.open(self._path(proxy_url, slot), os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return EgressLease(proxy_url, fd)
                except BlockingIOError:
                    os.close(fd)
        return None

    async def acquire(self, key: str, timeout: float = 300) -> EgressLease:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            lease = self.try_acquire(key)
            if lease:
                return lease
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("No outgoing IP slot became available before the queue timeout")
            await asyncio.sleep(min(0.25, remaining))

    def status(self) -> dict[str, dict[str, int]]:
        """Return process-safe live usage for every configured outgoing IP."""
        result = {}
        for proxy_url in self.proxies:
            active = 0
            for slot in range(self.slots_per_proxy):
                fd = os.open(self._path(proxy_url, slot), os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BlockingIOError:
                    active += 1
                finally:
                    os.close(fd)
            result[proxy_url or "direct"] = {
                "active": active,
                "limit": self.slots_per_proxy,
            }
        return result
