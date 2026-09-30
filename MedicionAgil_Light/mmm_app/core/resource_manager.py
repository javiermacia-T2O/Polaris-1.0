"""Gestor único de recursos: reservas atómicas, espera cancelable y conexiones.

Este módulo concentra tres responsabilidades que antes estaban repartidas y
sin coordinación:

1. **Admisión de memoria.** Un único punto decide si una operación pesada
   (conversión, join, agregación completa, sort completo, exportación o
   materialización científica) cabe en el presupuesto disponible. Las
   reservas son atómicas, la espera es cancelable y la liberación ocurre
   siempre en ``finally``.
2. **Perfiles por RAM.** Los límites son *perfiles configurables*, no
   garantías de RSS. Se derivan de la RAM total y se recortan además por la
   memoria realmente disponible.
3. **Conexiones DuckDB.** Como máximo dos conexiones reutilizables con
   propietario exclusivo durante toda la consulta y el consumo del reader.

Los controles ``health``/``cancel`` nunca esperan una reserva: no pasan por
este gestor. Las operaciones anidadas propagan el contexto del hilo en lugar
de adquirir una segunda reserva bloqueante.
"""

from __future__ import annotations

import ctypes
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from core import memory_budget
from core.tasks import TaskCancelled

GIB = 1024 ** 3
MIB = 1024 ** 2

# Operaciones que consumen el presupuesto "pesado". El resto son ligeras.
HEAVY_OPERATIONS = frozenset({
    "convert", "conversion", "join", "merge", "aggregate", "aggregation",
    "sort", "export", "materialize", "analysis", "run_analysis",
    "build_table", "materialize_table_result", "cache_as_parquet",
    "export_dataset", "export_result", "merge_datasets", "preview_table",
    "get_column_values", "get_date_range", "clear_cache",
})

# Máximo de consultas simultáneas y de ellas cuántas pueden ser pesadas.
MAX_ACTIVE_QUERIES = 2
MAX_HEAVY_QUERIES = 1


class ResourceUnavailable(MemoryError):
    """La operación no cabe en el presupuesto; el mensaje es accionable."""


class ResourceCancelled(TaskCancelled):
    """La espera por una reserva se canceló antes de concederse.

    Hereda de ``TaskCancelled`` para que las rutas existentes (sidecar,
    exportación, conversión) la traten como una cancelación cooperativa.
    """


@dataclass(frozen=True)
class ResourceProfile:
    """Perfil inicial por RAM total. Son techos, no garantías de RSS."""

    total_ram: int
    light_bytes: int
    heavy_bytes: int
    threads: int
    sidecar_ceiling: int
    os_reserve: int

    def reservation_for(self, kind: str) -> int:
        return self.heavy_bytes if kind == "heavy" else self.light_bytes


def profile_for(total_ram: int) -> ResourceProfile:
    """Selecciona el perfil según la RAM total del equipo."""
    if total_ram < 12 * GIB:
        light, heavy, threads, ceiling = 192 * MIB, 256 * MIB, 1, int(1.25 * GIB)
    elif total_ram < 24 * GIB:
        light, heavy, threads, ceiling = 256 * MIB, 384 * MIB, 2, 2 * GIB
    else:
        light, heavy, threads, ceiling = 384 * MIB, 768 * MIB, 4, 4 * GIB
    os_reserve = max(2 * GIB, int(total_ram * 0.25))
    return ResourceProfile(total_ram, light, heavy, threads, ceiling, os_reserve)


def _process_rss() -> int:
    """Working set del proceso actual (bytes); 0 si no se puede medir."""
    try:
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
    except (AttributeError, OSError):
        pass
    return 0


class ResourceManager:
    """Reservas atómicas con admisión por memoria disponible real."""

    def __init__(self, profile: ResourceProfile | None = None):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._profile = profile or profile_for(memory_budget.system_memory().total)
        self._active = 0
        self._heavy_active = 0
        # Reservas concedidas cuya memoria aún no está reflejada en el RSS.
        self._outstanding = 0
        # Bytes residentes (datasets/resultados) para el preflight de carga.
        self._resident = 0
        self._local = threading.local()

    # ---------------------------------------------------------------- perfil
    @property
    def profile(self) -> ResourceProfile:
        return self._profile

    def refresh_profile(self) -> ResourceProfile:
        """Recalcula el perfil si cambió la RAM total (p. ej. en tests)."""
        total = memory_budget.system_memory().total
        if total != self._profile.total_ram:
            self._profile = profile_for(total)
        return self._profile

    # ------------------------------------------------------------ admisión
    def _free_now(self) -> int:
        """Memoria adicional libre según la fórmula de admisión.

        ``min(techo sidecar − RSS sidecar, RAM disponible − reserva SO)``
        menos las reservas concedidas aún no consumidas. No se suma la
        memoria residente: el RSS ya la incluye, así que contarla otra vez
        sería doble cómputo.
        """
        rss = _process_rss()
        memory = memory_budget.system_memory()
        ceiling_free = max(0, self._profile.sidecar_ceiling - rss)
        os_free = max(0, memory.available - self._profile.os_reserve)
        return max(0, min(ceiling_free, os_free) - self._outstanding)

    def _system_headroom(self) -> int:
        """Techo del sistema ignorando nuestras propias reservas pendientes."""
        memory = memory_budget.system_memory()
        return max(0, memory.available - self._profile.os_reserve)

    def _fits(self, kind: str, amount: int) -> bool:
        if self._active >= MAX_ACTIVE_QUERIES:
            return False
        if kind == "heavy" and self._heavy_active >= MAX_HEAVY_QUERIES:
            return False
        return self._free_now() >= amount

    def _impossible(self, amount: int) -> str | None:
        """Motivo por el que la operación nunca cabrá, o ``None``."""
        if amount > self._profile.sidecar_ceiling:
            return (
                f"La operación necesita {amount / MIB:.0f} MiB y el techo de "
                f"la sesión es {self._profile.sidecar_ceiling / MIB:.0f} MiB. "
                "Reduce el periodo, las variables o los filtros.")
        if amount > self._system_headroom():
            return (
                f"La operación necesita {amount / MIB:.0f} MiB y solo hay "
                f"{self._system_headroom() / MIB:.0f} MiB libres tras la "
                "reserva del sistema. Cierra otras aplicaciones o reduce el "
                "alcance de la operación.")
        return None

    # ------------------------------------------------------------- reservas
    @contextmanager
    def reserve(self, kind: str = "light", amount: int | None = None,
                cancel: threading.Event | None = None,
                timeout: float | None = None):
        """Adquiere una reserva atómica; libera siempre en ``finally``.

        Las reservas anidadas en el mismo hilo propagan el contexto: no se
        adquiere una segunda reserva bloqueante.
        """
        held = getattr(self._local, "held", None)
        if held is not None:
            # Contexto propagado: la operación externa ya posee la reserva.
            yield
            return
        amount = int(amount if amount is not None
                     else self._profile.reservation_for(kind))
        self._acquire(kind, amount, cancel, timeout)
        self._local.held = (kind, amount)
        try:
            yield
        finally:
            self._local.held = None
            self._release(kind, amount)

    def _acquire(self, kind: str, amount: int,
                 cancel: threading.Event | None,
                 timeout: float | None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                if cancel is not None and cancel.is_set():
                    raise ResourceCancelled()
                if self._fits(kind, amount):
                    self._active += 1
                    if kind == "heavy":
                        self._heavy_active += 1
                    self._outstanding += amount
                    return
                reason = self._impossible(amount)
                if reason is not None:
                    raise ResourceUnavailable(reason)
                if deadline is not None and time.monotonic() >= deadline:
                    raise ResourceUnavailable(
                        "La operación está temporalmente ocupada: hay "
                        f"{self._active} consultas activas y "
                        f"{self._free_now() / MIB:.0f} MiB libres. "
                        "Vuelve a intentarlo en unos segundos.")
                self._cond.wait(0.1)

    def _release(self, kind: str, amount: int) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            if kind == "heavy":
                self._heavy_active = max(0, self._heavy_active - 1)
            self._outstanding = max(0, self._outstanding - amount)
            self._cond.notify_all()

    # ------------------------------------------------------------ residente
    def note_resident(self, amount: int) -> None:
        """Registra bytes residentes (datasets/resultados) para el preflight."""
        with self._lock:
            self._resident = max(0, self._resident + int(amount))

    def resident_bytes(self) -> int:
        with self._lock:
            return self._resident

    # ------------------------------------------------------------ diagnóstico
    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "active": self._active,
                "heavy_active": self._heavy_active,
                "outstanding": self._outstanding,
                "resident": self._resident,
                "free": self._free_now(),
                "rss": _process_rss(),
                "ceiling": self._profile.sidecar_ceiling,
                "os_reserve": self._profile.os_reserve,
                "threads": self._profile.threads,
            }


class _Slot:
    __slots__ = ("conn", "owner", "generation", "in_use")

    def __init__(self, conn, generation: int):
        self.conn = conn
        self.owner: int | None = None
        self.generation = generation
        self.in_use = False


class ConnectionPool:
    """Hasta ``max_size`` conexiones DuckDB reutilizables y exclusivas.

    Una conexión se presta a un único hilo durante toda la consulta y el
    consumo del reader. El préstamo es reentrante por hilo, de modo que una
    operación que llama varias veces a ``get_conn`` no se bloquea a sí misma.
    """

    def __init__(self, factory, max_size: int = 2):
        self._factory = factory
        self._max = max(1, int(max_size))
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._slots: list[_Slot] = []
        self._generation = 0
        self._local = threading.local()
        self._on_lease = None

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def set_lease_callback(self, callback) -> None:
        """Callback invocado al prestar una conexión (para vincular cancel)."""
        self._on_lease = callback

    def _new_slot(self) -> _Slot:
        self._generation += 1
        return _Slot(self._factory(), self._generation)

    def acquire(self, cancel: threading.Event | None = None,
                timeout: float | None = None):
        """Presta una conexión exclusiva al hilo actual (reentrante)."""
        held = getattr(self._local, "slot", None)
        if held is not None:
            held.depth += 1
            return held.slot.conn
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                if cancel is not None and cancel.is_set():
                    raise ResourceCancelled()
                slot = self._pick_free()
                if slot is not None:
                    slot.in_use = True
                    slot.owner = threading.get_ident()
                    self._local.slot = _Lease(slot)
                    if self._on_lease is not None:
                        try:
                            self._on_lease(slot.conn)
                        except Exception:
                            pass
                    return slot.conn
                if deadline is not None and time.monotonic() >= deadline:
                    raise ResourceUnavailable(
                        "Las dos conexiones de datos están ocupadas. "
                        "Espera a que termine la consulta en curso.")
                self._cond.wait(0.1)

    def _pick_free(self) -> _Slot | None:
        for slot in self._slots:
            if not slot.in_use:
                return slot
        if len(self._slots) < self._max:
            slot = self._new_slot()
            self._slots.append(slot)
            return slot
        # Reclaim leases held by threads that already finished: a worker that
        # died without releasing must not pin a connection forever.
        self._reclaim_dead()
        for slot in self._slots:
            if not slot.in_use:
                return slot
        return None

    def _reclaim_dead(self) -> None:
        """Libera préstamos cuyo hilo propietario ya no existe."""
        alive = {thread.ident for thread in threading.enumerate()}
        for slot in self._slots:
            if slot.in_use and slot.owner not in alive:
                slot.in_use = False
                slot.owner = None

    def release(self) -> None:
        """Libera el préstamo del hilo actual (idempotente)."""
        lease = getattr(self._local, "slot", None)
        if lease is None:
            return
        lease.depth -= 1
        if lease.depth > 0:
            return
        self._local.slot = None
        with self._cond:
            lease.slot.in_use = False
            lease.slot.owner = None
            self._cond.notify_all()

    def drop_lease(self) -> None:
        """Suelta por completo el préstamo del hilo actual, sea cual sea su
        profundidad. Se usa en las rutas legadas (``reset_conn``) que deben
        garantizar que la conexión queda libre para otros hilos."""
        lease = getattr(self._local, "slot", None)
        if lease is None:
            return
        self._local.slot = None
        with self._cond:
            lease.slot.in_use = False
            lease.slot.owner = None
            self._cond.notify_all()

    def current(self):
        """Conexión prestada al hilo actual, o ``None`` sin bloquear."""
        lease = getattr(self._local, "slot", None)
        return lease.slot.conn if lease is not None else None

    def conn_for_thread(self, ident: int):
        """Conexión vigente de un hilo concreto, o ``None``.

        Se resuelve por identidad de hilo, no por objeto: si la conexión se
        recreó, el vínculo apunta a la nueva sin re-registrar nada.
        """
        with self._lock:
            for slot in self._slots:
                if slot.owner == ident:
                    return slot.conn
        return None

    def interrupt_thread(self, ident: int) -> bool:
        """Interrumpe solo la operación activa de la conexión de ese hilo."""
        conn = self.conn_for_thread(ident)
        if conn is None:
            return False
        try:
            conn.interrupt()
            return True
        except Exception:
            return False

    def interrupt_current(self) -> bool:
        """Interrumpe solo la operación activa de la conexión del hilo."""
        conn = self.current()
        if conn is None:
            return False
        try:
            conn.interrupt()
            return True
        except Exception:
            return False

    def close_idle(self) -> int:
        """Cierra conexiones inactivas bajo presión de memoria."""
        closed = 0
        with self._cond:
            keep: list[_Slot] = []
            for slot in self._slots:
                if slot.in_use:
                    keep.append(slot)
                    continue
                try:
                    slot.conn.close()
                except Exception:
                    pass
                closed += 1
            self._slots = keep
        return closed

    def close_all(self) -> None:
        with self._cond:
            for slot in self._slots:
                try:
                    slot.conn.close()
                except Exception:
                    pass
            self._slots = []
        self._local.slot = None


class _Lease:
    __slots__ = ("slot", "depth")

    def __init__(self, slot: _Slot):
        self.slot = slot
        self.depth = 1


_MANAGER: ResourceManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> ResourceManager:
    """Gestor único del proceso."""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = ResourceManager()
    return _MANAGER


def reset_manager() -> None:
    """Reinicia el gestor (solo para tests)."""
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = None


__all__ = [
    "GIB", "MIB", "HEAVY_OPERATIONS", "MAX_ACTIVE_QUERIES",
    "MAX_HEAVY_QUERIES", "ResourceUnavailable", "ResourceCancelled",
    "ResourceProfile", "profile_for", "ResourceManager", "ConnectionPool",
    "get_manager", "reset_manager",
]
