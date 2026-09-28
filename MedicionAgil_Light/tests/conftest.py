import sys
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1] / "mmm_app"
sys.path.insert(0, str(PROJECT))


@pytest.fixture(autouse=True)
def stable_system_memory_for_unit_tests(monkeypatch):
    """Keep SQL tests independent of concurrent Windows memory pressure.

    Budget tests pass explicit memory scenarios or override this fixture.
    Production always reads the real system value.
    """
    from core import memory_budget

    monkeypatch.setattr(memory_budget, "system_memory", lambda: (
        memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                   8 * memory_budget.GIB)))
