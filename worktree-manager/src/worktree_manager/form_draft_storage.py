"""Per-write records in the existing local form-draft directory."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import tempfile
import time
import uuid


def _windows_move(source, destination, *, replace: bool) -> None:
    import ctypes
    from ctypes import wintypes

    move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    # WRITE_THROUGH publishes the name on disk before returning, including directories.
    if not move(str(source), str(destination), 0x8 | (0x1 if replace else 0)):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_directory(path: Path) -> None:
    if path.is_dir():
        return
    if path.parent == path:
        raise FileNotFoundError(errno.ENOENT, "Draft directory root is unavailable", str(path))
    _windows_directory(path.parent)
    temporary = Path(tempfile.mkdtemp(dir=path.parent))
    try:
        try:
            _windows_move(temporary, path, replace=False)
        except FileExistsError:
            if not path.is_dir():
                raise
    finally:
        if temporary.exists():
            temporary.rmdir()


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_draft(base: Path, binding, task_id, values, *, pending: bool) -> Path:
    path = base if binding is None else base.with_stem(f"{base.stem}-{uuid.uuid4().hex}")
    write_record(path, {
        "task_id": task_id, "binding": binding, "values": values,
        "pending": pending, "written_at_ns": time.time_ns(),
    })
    return path


def write_record(path: Path, record: dict) -> None:
    path = path.absolute()
    temporary = None
    try:
        if os.name == "nt":
            _windows_directory(path.parent)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            _windows_move(temporary, path, replace=True)
        else:
            os.replace(temporary, path)
            # Include ancestors on every attempt: a previous failed barrier may
            # have left an existing but not yet durable first-touch directory.
            for directory in path.parents:
                _sync_directory(directory)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def read_drafts(base: Path, binding) -> list[tuple[Path, dict]]:
    records = []
    paths = [base] if binding is None else base.parent.glob(f"{base.stem}-*.json")
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if binding is not None and isinstance(record, dict) and not {"pending", "written_at_ns"} <= record.keys():
                continue
            if (isinstance(record, dict) and isinstance(record.get("values"), dict)
                    and record.get("binding") == binding
                    and type(record.get("written_at_ns", 0)) is int
                    and type(record.get("pending", False)) is bool):
                records.append((path, record))
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda item: (item[1].get("written_at_ns", 0), item[0].name), reverse=True)
