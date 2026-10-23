"""Native connected-session admission at the existing Manager IPC boundary."""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime

from agent_procutil import no_window_kwargs

from .mux_child_process import run as run_mux_child
from .mux_mapping_registry import MuxMappingRegistry

KIND = "mux-discover-v1"
LOG = logging.getLogger(__name__)


def parse_rendered_at(value: str) -> float | None:
    """Best-effort ISO-8601 -> epoch-seconds parse for ordering renders.

    An unparseable render retains the existing unorderable/not-older contract;
    this fence rejects old renders, not an additional class of new requests.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def handle_discovery(
    registry: MuxMappingRegistry, payload: dict, runtime=None, handler_tracker=None,
) -> dict:
    from .mux_daemon_live import _mapping_to_observation

    if runtime is not None and not runtime.accepting_requests():
        return {"applied": False, "reason": "draining"}
    if handler_tracker is not None:
        handler_tracker.enter()
    try:
        result = discover_mapping(registry, payload)
    finally:
        if handler_tracker is not None:
            handler_tracker.exit()
    if result.get("applied"):
        result["entry"] = _mapping_to_observation(result["entry"])
    return result


def status_key_content(payload: dict, values: dict) -> dict:
    """Different mapping identities must not coalesce onto an earlier render."""
    return {"values": values, **{
        field: payload[field] for field in (
            "mapping_revision", "mux_session", "session_incarnation",
        ) if field in payload
    }}


def status_fence_failure(entry: dict, payload: dict) -> dict | None:
    if any(
        field in payload and payload[field] != entry.get(field)
        for field in ("mapping_revision", "mux_session", "session_incarnation")
    ):
        return {"applied": False, "reason": "stale-mapping"}
    if entry.get("native_session_incarnation"):
        confirmed = confirm_native_session(entry, connected_only=False)
        if not confirmed.get("applied"):
            return confirmed
    return None


def discover_mapping(registry: MuxMappingRegistry, payload: dict) -> dict:
    """Admit native-confirmed identity only against the pre-probe registry snapshot."""
    for key in ("project", "worktree_id", "worktree_path", "mux_session",
                "mux_bin", "session_incarnation"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError(f"mux-discover-v1 missing {key!r}")
    name = payload["mux_session"]
    if name != "wt-" + payload["worktree_id"].replace(".", "_"):
        return {"applied": False, "reason": "unattributed-session"}
    incumbent = registry.get(payload["project"], payload["worktree_id"])
    confirmed = confirm_native_session(payload, connected_only=True)
    if not confirmed.get("applied"):
        return confirmed
    return registry.discover(
        dict(payload, attached_clients=confirmed["attached_clients"]), expected=incumbent,
    )


def confirm_native_session(payload: dict, *, connected_only: bool) -> dict:
    """A failed enumeration remains unknown and cannot mutate a stored fence."""
    name = payload["mux_session"]
    try:
        probe = run_mux_child(
            [payload["mux_bin"], "list-sessions", "-F",
             "#{session_name}\t#{session_attached}\t#{session_id}:#{session_created}\t#{session_path}"],
            capture_output=True, text=True, timeout=2, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOG.warning("Connected mux discovery probe failed for %s: %s", name, exc)
        return {"applied": False, "reason": "enumeration-unavailable"}
    if probe.returncode != 0:
        return {"applied": False, "reason": "enumeration-unavailable"}
    matches = [line.split("\t", 3) for line in (probe.stdout or "").splitlines()
               if line.split("\t", 1)[0] == name]
    if len(matches) != 1 or len(matches[0]) != 4:
        return {"applied": False, "reason": "session-unconfirmed"}
    _, count, incarnation, path = matches[0]
    if incarnation != payload["session_incarnation"]:
        return {"applied": False, "reason": "stale-incarnation"}
    if not path or os.path.normcase(os.path.realpath(path)) != os.path.normcase(
        os.path.realpath(payload["worktree_path"])
    ):
        return {"applied": False, "reason": "unattributed-path"}
    if not count.isdecimal() or (connected_only and int(count) <= 0):
        return {"applied": False, "reason": "not-connected"}
    return {"applied": True, "attached_clients": int(count)}
