"""UI-independent application boundary for Medición Ágil.

The heavy application module (pandas, DuckDB, the scientific services) is
imported lazily. The sidecar must be able to answer ``health`` and control
operations before the scientific stack is loaded, so importing this package
must stay cheap.
"""

from __future__ import annotations

from typing import Any

__all__ = ["AppError", "JobState", "MedicionApplication"]

_LAZY = {
    "MedicionApplication": (".application", "MedicionApplication"),
    "AppError": (".errors", "AppError"),
    "JobState": (".schemas", "JobState"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
