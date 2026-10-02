"""Publicación segura de archivos generados en el mismo volumen de destino."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_output(path):
    """Entrega una ruta temporal y sustituye el destino solo al terminar bien."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=target.suffix,
                                dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    # DuckDB COPY exige que la ruta de salida aún no exista.
    temporary.unlink()
    try:
        yield temporary
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path, data):
    with atomic_output(path) as temporary:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
