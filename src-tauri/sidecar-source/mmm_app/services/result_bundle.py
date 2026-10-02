"""Publicación agrupada y segura de resultados de análisis.

Este módulo no conoce Tkinter.  Crea una carpeta de resultados con esta forma::

    <directorio elegido>/<dd-mm-yyyy>/<cliente>/<análisis> <HH-mm>/

Los archivos se escriben en una carpeta temporal y esta se publica de una vez
al terminar.  Cada archivo usa además la escritura atómica del exportador
existente.  Si la tarea se cancela o falla, la carpeta temporal se elimina y
no queda una carpeta de análisis incompleta visible para el usuario.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from core.atomic import atomic_output
from core.exporter import export_dataframe, save_figure_isolated
from core.tasks import TaskCancelled


_INVALID_COMPONENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_FORMAT_SUFFIXES = {"csv": ".csv", "tsv": ".tsv", "xlsx": ".xlsx",
                   "parquet": ".parquet"}
# Windows' legacy path limit is 260 UTF-16 code units.  Leave headroom for
# tempfile's random suffix and for applications which still use MAX_PATH,
# while keeping the full path valid on systems with long-path support too.
_MAX_SAFE_PATH_UNITS = 240
_TEMPFILE_NAME_OVERHEAD = 10  # '.', '-', and tempfile's random component
# A staging directory must leave room for at least a shortened output name
# and the temporary sibling created by atomic_output.
_MIN_OUTPUT_PATH_RESERVE = 35  # separator + 24-unit filename + temp overhead


def _path_units(value: str | os.PathLike[str]) -> int:
    """Return the length Windows uses for a path (UTF-16 code units)."""
    return len(os.fspath(value).encode("utf-16-le")) // 2


def _prefix_for_units(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    result: list[str] = []
    used = 0
    for char in value:
        units = _path_units(char)
        if used + units > limit:
            break
        result.append(char)
        used += units
    return "".join(result)


def _shorten_component(value: str, max_units: int) -> str:
    """Shorten a component while retaining a readable prefix and identity."""
    value = str(value)
    if _path_units(value) <= max_units:
        return value
    digest = hashlib.blake2s(value.encode("utf-8"), digest_size=5).hexdigest()
    marker = f"~{digest}"
    prefix = _prefix_for_units(value, max_units - _path_units(marker))
    return prefix.rstrip(" .") + marker


def _shorten_filename(value: str, max_units: int) -> str:
    """Shorten a filename, preserving its extension for file type detection."""
    suffix = Path(value).suffix
    stem = value[:-len(suffix)] if suffix else value
    digest = hashlib.blake2s(value.encode("utf-8"), digest_size=5).hexdigest()
    marker = f"~{digest}"
    prefix = _prefix_for_units(
        stem, max_units - _path_units(marker) - _path_units(suffix)
    )
    return prefix.rstrip(" .") + marker + suffix


def _fit_directory_component(value: str, parent: Path) -> str:
    available = (_MAX_SAFE_PATH_UNITS - _path_units(parent) - 1 -
                 _TEMPFILE_NAME_OVERHEAD - _MIN_OUTPUT_PATH_RESERVE)
    if available < 24:
        raise OSError(
            "La ruta de resultados es demasiado larga; elige una carpeta "
            "de salida más corta"
        )
    return _shorten_component(value, available)


def _fit_filename(value: str, parent: Path) -> str:
    available = (_MAX_SAFE_PATH_UNITS - _path_units(parent) - 1 -
                 _TEMPFILE_NAME_OVERHEAD)
    if available < 24:
        raise OSError(
            "La ruta de resultados es demasiado larga; elige una carpeta "
            "de salida más corta"
        )
    if _path_units(value) <= available:
        return value
    return _shorten_filename(value, available)


def _safe_component(value: str, *, fallback: str) -> str:
    """Devuelve un nombre de componente válido en Windows."""
    text = _INVALID_COMPONENT.sub("_", str(value)).strip().rstrip(".")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = fallback
    if text.upper() in _RESERVED_NAMES:
        text = f"_{text}"
    return _shorten_component(text, 120).rstrip(" .") or fallback


def _safe_filename(value: str, *, fallback: str) -> str:
    """Valida un nombre plano de archivo sin permitir traversal."""
    raw = str(value).replace("\\", "/")
    if "/" in raw:
        raise ValueError("El nombre de archivo debe ser plano")
    # Do not use _safe_component here: a figure can legitimately start with
    # '.01_...png', and truncating it as a generic component makes pathlib
    # mistake the leading dot for the extension. Keep the extension intact.
    text = _INVALID_COMPONENT.sub("_", raw).strip().rstrip(".")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = fallback
    if text.upper() in _RESERVED_NAMES:
        text = f"_{text}"
    return _shorten_filename(text, 120) if _path_units(text) > 120 else text


def _check_cancel(cancel: Any) -> None:
    if cancel is not None and cancel.is_set():
        raise TaskCancelled()


class ResultBundle:
    """Carpeta transaccional para tablas, archivos y gráficos de un análisis.

    Normalmente se usa como gestor de contexto. El contexto publica la carpeta
    al salir correctamente y la descarta si se produce una excepción::

        with create_result_bundle(root, "Cliente 1", "Regresión") as bundle:
            bundle.save_dataframe("tabla", frame, fmt="csv")
            bundle.save_figure("serie", figure)

    ``path`` es la ruta final esperada incluso mientras el contexto está
    abierto; solo se considera publicada después de ``commit``.
    """

    def __init__(self, client_dir: Path, stem: str, *, cancel=None,
                 progress=None):
        self._client_dir = client_dir
        self._stem = stem
        self._cancel = cancel
        self._progress = progress
        self._final_path = client_dir / stem
        self._staging = Path(tempfile.mkdtemp(prefix=f".{stem}-",
                                              dir=client_dir))
        self._committed = False
        self._discarded = False
        self._outputs: list[dict[str, Any]] = []

    @property
    def path(self) -> Path:
        """Ruta final de la carpeta publicada o pendiente de publicación."""
        return self._final_path

    @property
    def committed(self) -> bool:
        return self._committed

    def _ensure_open(self) -> None:
        if self._committed:
            raise RuntimeError("El paquete de resultados ya está publicado")
        if self._discarded:
            raise RuntimeError("El paquete de resultados fue descartado")

    def _report(self, fraction: float, detail: str) -> None:
        if self._progress is not None:
            self._progress((max(0.0, min(1.0, float(fraction))), detail))

    def _target(self, filename: str, fallback: str) -> Path:
        self._ensure_open()
        _check_cancel(self._cancel)
        name = _safe_filename(filename, fallback=fallback)
        # atomic_output creates a sibling temporary file whose name is longer
        # than the final output. Fit the user supplied name before mkstemp so
        # long analysis labels cannot surface as FileNotFoundError on Windows.
        name = _fit_filename(name, self._staging)
        target = self._staging / name
        if target.exists():
            raise FileExistsError(f"Ya existe el resultado '{name}'")
        return target

    def _record(self, target: Path, kind: str, **extra: Any) -> None:
        self._outputs.append({"name": target.name, "kind": kind, **extra})

    def save_dataframe(self, filename: str, frame, fmt: str = "csv", **kwargs) -> Path:
        """Exporta un DataFrame al paquete y devuelve su ruta temporal.

        ``fmt`` y el resto de opciones se delegan al exportador de datos
        existente.  ``cancel`` y ``progress`` no deben pasarse en ``kwargs``;
        el paquete los gestiona para mantener una cancelación coherente.
        """
        fmt = str(fmt).lower()
        if fmt not in _FORMAT_SUFFIXES:
            raise ValueError(f"Formato no soportado: {fmt}")
        name = _safe_filename(filename, fallback="tabla")
        if not Path(name).suffix:
            name += _FORMAT_SUFFIXES[fmt]
        target = self._target(name, fallback=f"tabla{_FORMAT_SUFFIXES[fmt]}")
        export_dataframe(frame, target, fmt, cancel=self._cancel,
                         progress=self._data_progress, **kwargs)
        _check_cancel(self._cancel)
        self._record(target, "table", format=fmt)
        return self._staging / target.name

    def _data_progress(self, update) -> None:
        try:
            fraction, detail = update
        except (TypeError, ValueError):
            fraction, detail = 0.0, str(update)
        self._report(fraction, str(detail))

    def save_figure(self, filename: str, figure) -> Path:
        """Guarda un gráfico Matplotlib de forma aislada y atómica."""
        name = _safe_filename(filename, fallback="grafico.png")
        if not Path(name).suffix:
            name += ".png"
        target = self._target(name, fallback="grafico.png")
        save_figure_isolated(figure, target, cancel=self._cancel)
        _check_cancel(self._cancel)
        self._record(target, "figure")
        return self._staging / target.name

    def save_bytes(self, filename: str, data: bytes) -> Path:
        """Guarda bytes (por ejemplo un JSON o una imagen ya serializada)."""
        target = self._target(filename, fallback="resultado.bin")
        with atomic_output(target) as temporary:
            with temporary.open("wb") as stream:
                view = memoryview(data)
                for start in range(0, len(view), 1024 * 1024):
                    _check_cancel(self._cancel)
                    stream.write(view[start:start + 1024 * 1024])
                stream.flush()
                os.fsync(stream.fileno())
        _check_cancel(self._cancel)
        self._record(target, "file")
        return self._staging / target.name

    def save_text(self, filename: str, text: str, *, encoding: str = "utf-8") -> Path:
        """Guarda texto con publicación atómica."""
        return self.save_bytes(filename, str(text).encode(encoding))

    def save_file(self, filename: str, source: str | os.PathLike[str]) -> Path:
        """Copia un archivo existente al paquete en bloques cancelables."""
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        target = self._target(filename, fallback=source_path.name)
        with atomic_output(target) as temporary:
            with source_path.open("rb") as src, temporary.open("wb") as dst:
                while True:
                    _check_cancel(self._cancel)
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    dst.write(block)
                dst.flush()
                os.fsync(dst.fileno())
        _check_cancel(self._cancel)
        self._record(target, "file", source=str(source_path))
        return self._staging / target.name

    def commit(self) -> Path:
        """Publica el paquete completo y devuelve su carpeta final."""
        self._ensure_open()
        _check_cancel(self._cancel)
        manifest = {
            "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "outputs": list(self._outputs),
        }
        manifest_path = self._staging / "manifest.json"
        with atomic_output(manifest_path) as temporary:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
        _check_cancel(self._cancel)
        candidate = self._final_path
        index = 1
        while True:
            try:
                os.replace(self._staging, candidate)
                self._final_path = candidate
                self._committed = True
                self._report(1.0, "Resultados guardados")
                return candidate
            except FileExistsError:
                index += 1
                candidate = self._client_dir / f"{self._stem} ({index})"

    def discard(self) -> None:
        """Descarta una publicación pendiente y sus temporales."""
        if self._committed:
            return
        self._discarded = True
        shutil.rmtree(self._staging, ignore_errors=True)

    def __enter__(self) -> "ResultBundle":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is None:
            try:
                self.commit()
            except BaseException:
                self.discard()
                raise
        else:
            self.discard()
        return False


def _next_stem(client_dir: Path, analysis_name: str, when: _dt.datetime) -> str:
    base = f"{_safe_component(analysis_name, fallback='Análisis')} {when:%H-%M}"
    base = _fit_directory_component(base, client_dir)
    candidate = base
    index = 1
    while (client_dir / candidate).exists():
        index += 1
        candidate = _fit_directory_component(f"{base} ({index})", client_dir)
    return candidate


def create_result_bundle(root: str | os.PathLike[str], client_name: str,
                         analysis_name: str, *, now: _dt.datetime | _dt.date | None = None,
                         client_directory: str | os.PathLike[str] | None = None,
                         cancel=None, progress=None) -> ResultBundle:
    """Crea un paquete pendiente de publicación.

    ``client_name`` se reutiliza si ya existe bajo la carpeta de fecha. Para
    una carpeta seleccionada explícitamente se puede pasar ``client_directory``;
    las rutas absolutas deben estar dentro de ``root`` y las relativas se
    resuelven bajo la carpeta de fecha actual.
    """
    _check_cancel(cancel)
    root_path = Path(root)
    moment = now or _dt.datetime.now()
    if isinstance(moment, _dt.date) and not isinstance(moment, _dt.datetime):
        moment = _dt.datetime.combine(moment, _dt.time.min)
    day_dir = root_path / moment.strftime("%d-%m-%Y")
    day_dir.mkdir(parents=True, exist_ok=True)
    if client_directory is None:
        client_name = _fit_directory_component(
            _safe_component(client_name, fallback="Cliente"), day_dir
        )
        client_dir = day_dir / client_name
    else:
        selected = Path(client_directory)
        client_dir = selected if selected.is_absolute() else day_dir / selected
        try:
            client_dir.resolve().relative_to(day_dir.resolve())
        except ValueError as exc:
            raise ValueError("La carpeta del cliente debe estar dentro de la fecha actual") from exc
    client_dir.mkdir(parents=True, exist_ok=True)
    stem = _next_stem(client_dir, analysis_name, moment)
    _check_cancel(cancel)
    return ResultBundle(client_dir, stem, cancel=cancel, progress=progress)


def save_result_bundle(root: str | os.PathLike[str], client_name: str,
                       analysis_name: str, *, tables: Mapping[str, Any] | None = None,
                       figures: Mapping[str, Any] | None = None,
                       files: Mapping[str, Any] | None = None,
                       now: _dt.datetime | _dt.date | None = None,
                       client_directory: str | os.PathLike[str] | None = None,
                       cancel=None, progress=None) -> ResultBundle:
    """Guarda tablas, gráficos y archivos y publica el paquete.

    ``tables`` acepta ``{nombre: dataframe}`` o ``{nombre: (dataframe, fmt)}``.
    ``figures`` acepta ``{nombre: matplotlib_figure}`` y ``files`` acepta
    ``{nombre: ruta}`` o ``{nombre: bytes}``.
    """
    bundle = create_result_bundle(
        root, client_name, analysis_name, now=now,
        client_directory=client_directory, cancel=cancel, progress=None)
    try:
        with bundle:
            tables = tables or {}
            total = max(1, len(tables) + len(figures or {}) + len(files or {}))
            done = 0

            def scoped_progress(update) -> None:
                """Convierte el avance de cada salida en avance global."""
                try:
                    fraction, detail = update
                except (TypeError, ValueError):
                    fraction, detail = 0.0, str(update)
                if progress is not None:
                    overall = min(1.0, (done + max(0.0, min(1.0,
                                                               float(fraction)))) / total)
                    progress((overall, str(detail)))

            bundle._progress = scoped_progress
            for name, value in tables.items():
                if isinstance(value, tuple) and len(value) == 2:
                    frame, fmt = value
                else:
                    frame, fmt = value, "csv"
                bundle.save_dataframe(name, frame, fmt=fmt)
                done += 1
                bundle._report(0.0, f"Tabla guardada: {name}")
            for name, figure in (figures or {}).items():
                bundle.save_figure(name, figure)
                done += 1
                bundle._report(0.0, f"Gráfico guardado: {name}")
            for name, source in (files or {}).items():
                if isinstance(source, (bytes, bytearray, memoryview)):
                    bundle.save_bytes(name, bytes(source))
                else:
                    bundle.save_file(name, source)
                done += 1
                bundle._report(0.0, f"Archivo guardado: {name}")
        return bundle
    except BaseException:
        bundle.discard()
        raise


__all__ = ["ResultBundle", "create_result_bundle", "save_result_bundle"]
