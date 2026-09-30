"""Process-safe layout and bounded policy for Polaris' disk cache."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import threading
import time

from core.runtime_paths import user_cache_root

GIB = 1024 ** 3


@dataclass(frozen=True)
class CacheLayout:
    root: Path
    reusable: Path
    results: Path
    temporary: Path
    session: Path


class CacheStore:
    """Own cache generations and evict only unreferenced reusable files."""

    def __init__(self, root: Path | None = None):
        root = Path(root or user_cache_root())
        session_id = f"{os.getpid()}-{int(time.time())}"
        self.layout = CacheLayout(root, root / "reusable", root / "results",
                                  root / "tmp", root / "sessions" / session_id)
        for path in self.layout.__dict__.values():
            path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._references: dict[Path, int] = {}
        (self.layout.session / "owner.json").write_text(json.dumps({
            "pid": os.getpid(), "created": time.time()}), encoding="utf-8")
        self.cleanup_orphans()

    def limits(self) -> tuple[int, int]:
        usage = shutil.disk_usage(self.layout.root)
        return min(20 * GIB, usage.total // 10), max(2 * GIB, usage.total // 20)

    def ensure_space(self, estimated: int = 0) -> None:
        quota, reserve = self.limits()
        usage = shutil.disk_usage(self.layout.root)
        if usage.free - estimated < reserve:
            self.evict(max(estimated + reserve - usage.free, 0))
            usage = shutil.disk_usage(self.layout.root)
        if usage.free - estimated < reserve or self.size() + estimated > quota:
            self.evict(max(self.size() + estimated - quota, 0))
        if shutil.disk_usage(self.layout.root).free - estimated < reserve:
            raise OSError("Espacio de disco insuficiente para la operación")

    def pin(self, path: Path) -> None:
        path = Path(path).resolve()
        with self._lock:
            self._references[path] = self._references.get(path, 0) + 1

    def unpin(self, path: Path) -> None:
        path = Path(path).resolve()
        with self._lock:
            count = self._references.get(path, 0) - 1
            if count > 0:
                self._references[path] = count
            else:
                self._references.pop(path, None)

    def size(self) -> int:
        return sum(path.stat().st_size for directory in
                   (self.layout.reusable, self.layout.results, self.layout.temporary)
                   for path in directory.rglob("*") if path.is_file())

    def evict(self, required: int = 0) -> int:
        now, removed = time.time(), 0
        candidates = []
        with self._lock:
            pinned = set(self._references)
        for path in self.layout.reusable.rglob("*"):
            if path.is_file() and path.resolve() not in pinned:
                ttl = 7 * 86400 if "table_results" in path.parts else 30 * 86400
                candidates.append((path.stat().st_atime, ttl, path))
        for accessed, ttl, path in sorted(candidates):
            if required <= removed and now - accessed <= ttl:
                continue
            try:
                removed += path.stat().st_size
                path.unlink()
            except OSError:
                pass
        return removed

    def cleanup_orphans(self) -> None:
        cutoff = time.time() - 24 * 3600
        sessions = self.layout.root / "sessions"
        for directory in sessions.iterdir():
            if directory == self.layout.session or not directory.is_dir():
                continue
            owner = directory / "owner.json"
            try:
                data = json.loads(owner.read_text(encoding="utf-8"))
                pid, created = int(data["pid"]), float(data["created"])
                alive = pid > 0 and Path(f"/proc/{pid}").exists() if os.name != "nt" else _pid_alive_windows(pid)
                if not alive and created < cutoff:
                    shutil.rmtree(directory)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue


def _pid_alive_windows(pid: int) -> bool:
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


_STORE: CacheStore | None = None
_LOCK = threading.Lock()


def get_cache_store() -> CacheStore:
    global _STORE
    if _STORE is None:
        with _LOCK:
            if _STORE is None:
                _STORE = CacheStore()
    return _STORE
