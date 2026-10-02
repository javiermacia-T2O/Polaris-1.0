"""FASE 3 · Bloque 2: gestor único de recursos y pool de conexiones.

Estos tests fijan el contrato del gestor de recursos:

* admisión concurrente (máximo 2 consultas, de ellas 1 pesada),
* error y cancelación sin fugas de reservas,
* espera cancelable,
* conexión recreada y seguimiento por identidad de hilo,
* cancelación tardía que no afecta al siguiente trabajo,
* ``health`` disponible bajo carga,
* perfiles por RAM y mensajes accionables.

Se conservan los gates IPC existentes (``test_sidecar_lazy_startup`` y
``test_engine_threads``), que siguen pasando sin cambios.
"""

from __future__ import annotations

import threading
import time

import pytest

from core import memory_budget
from core.resource_manager import (
    GIB,
    MIB,
    MAX_ACTIVE_QUERIES,
    MAX_HEAVY_QUERIES,
    ConnectionPool,
    ResourceCancelled,
    ResourceManager,
    ResourceUnavailable,
    profile_for,
    reset_manager,
)


# --------------------------------------------------------------------- utils
def _manager(total_gib: int = 16, available_gib: int = 8) -> ResourceManager:
    """Gestor con memoria simulada, independiente del equipo real."""
    profile = profile_for(total_gib * GIB)
    manager = ResourceManager(profile)
    manager._memory = memory_budget.MemoryStatus(total_gib * GIB,
                                                 available_gib * GIB)
    return manager


@pytest.fixture(autouse=True)
def _patch_memory(monkeypatch):
    """Memoria estable para todos los tests de este módulo."""
    monkeypatch.setattr(memory_budget, "system_memory", lambda: (
        memory_budget.MemoryStatus(16 * GIB, 8 * GIB)))
    yield
    reset_manager()


# ------------------------------------------------------------------- perfiles
def test_profile_scales_with_total_ram():
    small = profile_for(8 * GIB)
    medium = profile_for(16 * GIB)
    large = profile_for(32 * GIB)

    assert small.threads == 1 and medium.threads == 2 and large.threads == 4
    assert small.heavy_bytes < medium.heavy_bytes < large.heavy_bytes
    assert small.sidecar_ceiling < medium.sidecar_ceiling < large.sidecar_ceiling
    # La reserva del SO se limita a 1 GiB para que las operaciones ligeras
    # sigan disponibles cuando Windows informa de poca memoria libre.
    assert small.os_reserve == int(0.8 * GIB)
    assert large.os_reserve == 1 * GIB


def test_light_reservation_is_admitted_with_limited_free_ram(monkeypatch):
    manager = _manager()
    monkeypatch.setattr(memory_budget, "system_memory", lambda: (
        memory_budget.MemoryStatus(16 * GIB, int(1.5 * GIB))))

    with manager.reserve("light"):
        assert manager.snapshot()["active"] == 1


def test_reservation_for_selects_heavy_or_light():
    profile = profile_for(16 * GIB)
    assert profile.reservation_for("heavy") == profile.heavy_bytes
    assert profile.reservation_for("light") == profile.light_bytes
    assert profile.reservation_for("cualquiera") == profile.light_bytes


# ------------------------------------------------------------------ admisión
def test_concurrent_admission_caps_active_and_heavy_queries():
    manager = _manager()
    entered: list[str] = []
    release = threading.Event()
    go = threading.Event()

    def worker(kind: str):
        go.wait(timeout=10)
        with manager.reserve(kind, amount=1 * MIB):
            entered.append(kind)
            release.wait(timeout=10)

    threads = [threading.Thread(target=worker, args=(kind,))
               for kind in ("heavy", "light", "light")]
    for thread in threads:
        thread.start()
    go.set()
    # Deja que los tres intenten entrar; solo dos pueden estar activos.
    time.sleep(0.3)
    snapshot = manager.snapshot()
    assert snapshot["active"] <= MAX_ACTIVE_QUERIES
    assert snapshot["heavy_active"] <= MAX_HEAVY_QUERIES
    release.set()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert manager.snapshot()["active"] == 0
    assert manager.snapshot()["outstanding"] == 0


def test_second_heavy_query_waits_for_the_first():
    manager = _manager()
    order: list[str] = []
    first_inside = threading.Event()
    release_first = threading.Event()

    def first():
        with manager.reserve("heavy", amount=1 * MIB):
            order.append("first-in")
            first_inside.set()
            release_first.wait(timeout=10)
            order.append("first-out")

    def second():
        first_inside.wait(timeout=10)
        with manager.reserve("heavy", amount=1 * MIB):
            order.append("second-in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    # La segunda pesada no puede entrar mientras la primera está activa.
    assert order == ["first-in"]
    release_first.set()
    for thread in threads:
        thread.join(timeout=10)
    assert order == ["first-in", "first-out", "second-in"]


def test_impossible_reservation_reports_actionable_reason():
    manager = _manager()
    with pytest.raises(ResourceUnavailable) as excinfo:
        with manager.reserve("heavy", amount=manager.profile.sidecar_ceiling + 1):
            pass
    message = str(excinfo.value)
    assert "MiB" in message
    assert "Reduce" in message or "reduce" in message
    # Un fallo de admisión no deja reservas colgadas.
    assert manager.snapshot()["active"] == 0
    assert manager.snapshot()["outstanding"] == 0


def test_reservation_is_released_when_the_body_raises():
    manager = _manager()
    with pytest.raises(RuntimeError):
        with manager.reserve("heavy", amount=1 * MIB):
            raise RuntimeError("boom")
    snapshot = manager.snapshot()
    assert snapshot["active"] == 0
    assert snapshot["heavy_active"] == 0
    assert snapshot["outstanding"] == 0


def test_nested_reservations_propagate_the_thread_context():
    manager = _manager()
    with manager.reserve("heavy", amount=1 * MIB):
        with manager.reserve("heavy", amount=1 * MIB):
            # La reserva anidada no adquiere una segunda reserva bloqueante.
            assert manager.snapshot()["active"] == 1
    assert manager.snapshot()["active"] == 0


# ------------------------------------------------------- espera cancelable
def test_waiting_for_a_reservation_is_cancellable():
    manager = _manager()
    cancel = threading.Event()
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def holder():
        with manager.reserve("heavy", amount=1 * MIB):
            holder_ready.set()
            release_holder.wait(timeout=10)

    thread = threading.Thread(target=holder)
    thread.start()
    holder_ready.wait(timeout=10)

    cancel.set()
    started = time.monotonic()
    with pytest.raises(ResourceCancelled):
        with manager.reserve("heavy", amount=1 * MIB, cancel=cancel):
            pass
    assert time.monotonic() - started < 5

    release_holder.set()
    thread.join(timeout=10)
    assert manager.snapshot()["active"] == 0


def test_timeout_raises_without_leaking_the_reservation():
    manager = _manager()
    release_holder = threading.Event()
    holder_ready = threading.Event()

    def holder():
        with manager.reserve("heavy", amount=1 * MIB):
            holder_ready.set()
            release_holder.wait(timeout=10)

    thread = threading.Thread(target=holder)
    thread.start()
    holder_ready.wait(timeout=10)
    with pytest.raises(ResourceUnavailable):
        with manager.reserve("heavy", amount=1 * MIB, timeout=0.2):
            pass
    release_holder.set()
    thread.join(timeout=10)
    assert manager.snapshot()["active"] == 0
    assert manager.snapshot()["outstanding"] == 0


# ------------------------------------------------------------- residente
def test_resident_bytes_are_tracked_and_never_negative():
    manager = _manager()
    manager.note_resident(100 * MIB)
    assert manager.resident_bytes() == 100 * MIB
    manager.note_resident(-40 * MIB)
    assert manager.resident_bytes() == 60 * MIB
    manager.note_resident(-999 * MIB)
    assert manager.resident_bytes() == 0


# --------------------------------------------------------- pool de conexiones
class _FakeConn:
    """Conexión falsa que registra interrupciones y cierres."""

    def __init__(self, name: str):
        self.name = name
        self.interrupts = 0
        self.closed = False

    def interrupt(self):
        self.interrupts += 1

    def close(self):
        self.closed = True


def test_pool_reuses_connections_up_to_the_limit():
    created: list[_FakeConn] = []

    def factory():
        conn = _FakeConn(f"c{len(created)}")
        created.append(conn)
        return conn

    pool = ConnectionPool(factory, max_size=2)
    first = pool.acquire()
    pool.release()
    second = pool.acquire()
    assert second is first  # reutilizada, no recreada
    pool.release()
    assert len(created) == 1


def test_pool_lease_is_reentrant_per_thread():
    pool = ConnectionPool(lambda: _FakeConn("c"), max_size=2)
    outer = pool.acquire()
    inner = pool.acquire()
    assert inner is outer
    pool.release()
    # El préstamo sigue vivo: la profundidad era 2.
    assert pool.current() is outer
    pool.release()
    assert pool.current() is None


def test_pool_blocks_a_third_concurrent_lease():
    pool = ConnectionPool(lambda: _FakeConn("c"), max_size=2)
    go = threading.Event()
    release = threading.Event()
    acquired: list[int] = []

    def worker():
        go.wait(timeout=10)
        conn = pool.acquire(timeout=10)
        acquired.append(id(conn))
        release.wait(timeout=10)
        pool.release()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    go.set()
    time.sleep(0.3)
    # Solo dos préstamos simultáneos; el tercero espera.
    assert len(acquired) == 2
    release.set()
    for thread in threads:
        thread.join(timeout=10)
    assert len(acquired) == 3


def test_pool_interrupts_only_the_owning_thread():
    pool = ConnectionPool(lambda: _FakeConn("c"), max_size=2)
    conn = pool.acquire()
    ident = threading.get_ident()
    assert pool.interrupt_thread(ident) is True
    assert conn.interrupts == 1
    # Un hilo desconocido no interrumpe nada.
    assert pool.interrupt_thread(ident + 12345) is False
    assert conn.interrupts == 1
    pool.release()


def test_pool_follows_a_recreated_connection_by_thread_identity():
    created: list[_FakeConn] = []

    def factory():
        conn = _FakeConn(f"c{len(created)}")
        created.append(conn)
        return conn

    pool = ConnectionPool(factory, max_size=2)
    ident = threading.get_ident()
    first = pool.acquire()
    pool.release()
    pool.close_idle()  # retira la conexión inactiva
    second = pool.acquire()
    assert second is not first
    # El vínculo por identidad de hilo apunta a la conexión nueva.
    assert pool.conn_for_thread(ident) is second
    assert pool.interrupt_thread(ident) is True
    assert second.interrupts == 1
    assert first.interrupts == 0
    pool.release()


def test_late_cancellation_does_not_affect_the_next_job():
    manager = _manager()
    cancel = threading.Event()
    with manager.reserve("heavy", amount=1 * MIB, cancel=cancel):
        pass
    # El evento se activa tarde: la reserva ya se liberó y el siguiente
    # trabajo con un evento nuevo entra sin problema.
    cancel.set()
    with manager.reserve("heavy", amount=1 * MIB,
                         cancel=threading.Event()):
        assert manager.snapshot()["active"] == 1
    assert manager.snapshot()["active"] == 0


# ------------------------------------------------------------- integración
def test_engine_connection_is_released_and_recreated():
    from core import engine

    engine.reset_conn()
    first = engine.get_conn()
    engine.release_conn()
    second = engine.get_conn()
    assert second is first  # reutilizada por el pool
    engine.reset_conn()
    third = engine.get_conn()
    assert third is not first  # recreada tras reset_conn
    engine.release_conn()


def test_engine_file_views_follow_a_recreated_connection(tmp_path):
    from core import engine

    source = tmp_path / "datos.csv"
    source.write_text("key,value\n1,42\n", encoding="utf-8")
    engine.reset_conn()
    try:
        engine.register_file_in_duckdb(source, name="_rm_test_view")
        assert engine.query("SELECT value FROM _rm_test_view").iloc[0, 0] == 42
        # Al recrear la conexión, la vista debe volver a aplicarse sola.
        engine.reset_conn()
        assert engine.query("SELECT value FROM _rm_test_view").iloc[0, 0] == 42
    finally:
        engine.reset_conn()
        engine._FILE_VIEWS.pop("_rm_test_view", None)
        engine._FILE_VIEWS_VERSION += 1


def test_health_stays_available_while_heavy_work_is_reserved():
    """``health`` no pasa por el gestor: responde aunque haya carga."""
    from medicion_core.sidecar import SidecarServer

    manager = _manager()
    server = SidecarServer("t" * 32)
    with manager.reserve("heavy", amount=1 * MIB):
        response = server.handle({"id": "1", "token": "t" * 32,
                                  "operation": "health"})
    assert response["ok"] is True
    assert response["result"] == {"status": "ok", "protocol": 1}
    assert server._application is None