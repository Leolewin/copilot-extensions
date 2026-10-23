"""Connected native mux discovery uses engine records, never shell cwd."""

from types import SimpleNamespace
import subprocess

import pytest

from agent_worktrees import status_monitor_discovery as discovery


@pytest.fixture(autouse=True)
def matching_native_path(monkeypatch):
    original = discovery._session_path_matches
    monkeypatch.setattr(discovery, "_session_path_matches", lambda *args: True)
    monkeypatch.setattr(discovery, "_register_standalone", lambda *args: True)
    monkeypatch.setattr(discovery, "_last_session", None)
    monkeypatch.setattr(discovery, "_retry_after", {}, raising=False)
    return original


def _record(wt_id, path):
    return SimpleNamespace(worktree_id=wt_id, worktree_path=str(path), status="active")


def test_manual_reattach_discovers_all_connected_tracked_sessions(monkeypatch, tmp_path):
    records = [("project", _record("one.local", tmp_path)),
               ("other", _record("two", tmp_path))]
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: records)
    monkeypatch.setattr(discovery, "discover_via_manager", lambda payload: None)
    live = {"wt-one_local": (1, "$1:100"), "wt-two": (2, "$2:100"),
            "wt-foreign": (1, "$3:100"), "shell": (1, "$4:100")}
    assert discovery.discover_connected_worktrees(live, "mux", None) == {
        "wt-one_local": str(tmp_path), "wt-two": str(tmp_path),
    }


@pytest.mark.parametrize("live", [None, {}, {"wt-one": (0, "$1:100")},
                                        {"wt-one": (1, "")}])
def test_unknown_or_disconnected_enumeration_cannot_adopt(monkeypatch, tmp_path, live):
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])
    monkeypatch.setattr(discovery, "discover_via_manager",
                        lambda payload: pytest.fail("must not publish unknown/disconnected"))
    assert discovery.discover_connected_worktrees(live, "mux", None) == {}


def test_ambiguous_canonical_identity_is_not_adopted(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: [
        ("a", _record("one.local", tmp_path)),
        ("b", _record("one_local", tmp_path)),
    ])
    assert discovery.discover_connected_worktrees(
        {"wt-one_local": (1, "$1:100")}, "mux", None,
    ) == {}


def test_manager_refusal_never_falls_back_to_direct_presentation(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])
    monkeypatch.setattr(discovery, "discover_via_manager",
                        lambda payload: {"applied": False, "reason": "incumbent"})
    assert discovery.discover_connected_worktrees(
        {"wt-one": (1, "$1:100")}, "mux", None,
    ) == {}


def test_foreign_shell_at_known_name_is_not_attribution(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])
    monkeypatch.setattr(discovery, "_session_path_matches", lambda *args: False)
    monkeypatch.setattr(discovery, "discover_via_manager",
                        lambda payload: pytest.fail("foreign shell must not publish"))
    assert discovery.discover_connected_worktrees(
        {"wt-one": (1, "$1:100")}, "mux", None,
    ) == {}


def test_discovery_is_bounded_and_round_robin(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: [
        ("project", _record(str(i), tmp_path)) for i in range(4)
    ])
    monkeypatch.setattr(discovery, "discover_via_manager", lambda payload: None)
    live = {f"wt-{i}": (1, f"${i}:100") for i in range(4)}
    first = discovery.discover_connected_worktrees(live, "mux", None)
    second = discovery.discover_connected_worktrees(live, "mux", None)
    assert len(first) == len(second) == 2
    assert set(first) | set(second) == set(live)


def test_sweep_recovers_manual_native_attachments_without_launch_registration(
    monkeypatch, tmp_path,
):
    from agent_worktrees import __main__ as monitor

    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: [
        ("project", _record("one", tmp_path)), ("project", _record("two", tmp_path)),
    ])
    monkeypatch.setattr(discovery, "discover_via_manager", lambda payload: None)
    monkeypatch.setattr(monitor, "_monitor_registry_dir", lambda: tmp_path / "empty-registry")
    monkeypatch.setattr(monitor, "_monitor_list_sessions", lambda mux: {
        "wt-one": (1, "$1:100"), "wt-two": (2, "$2:100"),
    })
    monkeypatch.setattr(monitor, "_activate_project_for_path",
                        lambda *a, **kw: monitor.cfg.set_active_project("project"))
    monkeypatch.setattr(monitor, "_render_status_context", lambda *a, **kw: "CTX")
    monkeypatch.setattr(monitor, "_render_status_segment", lambda *a, **kw: "SEG")
    monkeypatch.setattr(monitor, "_monitor_maybe_trigger_handoff_cutover", lambda *a, **kw: None)
    monkeypatch.setattr(monitor, "_warm_list_cache_for_active_project", lambda **kw: 0)
    monkeypatch.setattr(monitor.tracking, "list_records", lambda *a, **kw: [])
    writes = []
    monkeypatch.setattr(monitor, "_monitor_mux_set",
                        lambda mux, name, option, value: writes.append((name, option)) or True)
    assert monitor._monitor_sweep("mux", "token", "prefix", set()) == 2
    assert ("wt-one", "@aw_seg") in writes
    assert ("wt-two", "@aw_seg") in writes


@pytest.mark.parametrize("mux,target", [("psmux", "wt-one"), ("tmux", "=wt-one")])
def test_native_original_path_probe_uses_owner_target_contract(
    monkeypatch, tmp_path, matching_native_path, mux, target,
):
    calls = []
    monkeypatch.setattr(discovery.subprocess, "run", lambda argv, **kw: (
        calls.append((argv, kw)) or subprocess.CompletedProcess(argv, 0, stdout=str(tmp_path))
    ))
    assert matching_native_path(mux, "wt-one", str(tmp_path))
    assert calls[0][0] == [mux, "display-message", "-p", "-t", target, "#{session_path}"]
    assert calls[0][1]["timeout"] == 1


@pytest.mark.parametrize("stdout,code", [("", 0), ("foreign", 0), ("workspace", 1)])
def test_unknown_or_wrong_original_path_is_not_owned(
    monkeypatch, matching_native_path, stdout, code,
):
    monkeypatch.setattr(discovery.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, code, stdout=stdout))
    assert not matching_native_path("mux", "wt-one", "workspace")


def test_manager_admission_populates_existing_managed_union(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from agent_worktrees import mux_link, status_monitor_runtime

    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])

    def admitted(payload):
        return {"applied": True, "entry": dict(
            payload, live=True, mapping_revision=1, panes=[],
            observed_at=datetime.now(timezone.utc).isoformat(),
        )}

    monkeypatch.setattr(discovery, "discover_via_manager", admitted)
    cache = mux_link.ManagedMuxCache(tmp_path / "cache.json")
    assert discovery.discover_connected_worktrees(
        {"wt-one": (1, "$1:100")}, "mux", cache,
    ) == {}, "Manager admission must never enter the standalone writer lane"
    _, observed, served = status_monitor_runtime._monitor_managed_session_union(cache, {})
    assert observed == {"wt-one"}
    assert served == [("wt-one", str(tmp_path))]


def test_discovery_response_cannot_regress_a_cached_revision(monkeypatch, tmp_path):
    from agent_worktrees import mux_link

    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])
    cache = mux_link.ManagedMuxCache(tmp_path / "cache.json")
    incumbent = dict(project="project", worktree_id="one", mux_session="new-owner",
                     session_incarnation="new", mapping_revision=9, live=True)
    assert cache.apply_observation(incumbent)["applied"]
    monkeypatch.setattr(discovery, "discover_via_manager", lambda payload: {
        "applied": True, "entry": dict(payload, mapping_revision=1, live=True),
    })
    assert discovery.discover_connected_worktrees(
        {"wt-one": (1, "$1:100")}, "mux", cache,
    ) == {}
    assert cache.get("project", "one")["mapping_revision"] == 9


def test_standalone_discovery_retains_path_for_subsequent_sweeps(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])
    monkeypatch.setattr(discovery, "discover_via_manager", lambda payload: None)
    registrations = []
    monkeypatch.setattr(discovery, "_register_standalone",
                        lambda *args: registrations.append(args) or True)
    assert discovery.discover_connected_worktrees(
        {"wt-one": (1, "$1:100")}, "mux", None,
    ) == {"wt-one": str(tmp_path)}
    assert registrations == [("wt-one", str(tmp_path))]


def test_healthy_registered_paths_add_no_scan_probe_or_ipc(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: pytest.fail("healthy registered roster must not be scanned"))
    monkeypatch.setattr(discovery, "_session_path_matches",
                        lambda *a: pytest.fail("healthy registered mux must not be re-probed"))
    monkeypatch.setattr(discovery, "discover_via_manager",
                        lambda *a: pytest.fail("healthy registered mux must not re-register"))
    assert discovery.discover_connected_worktrees(
        {"wt-one": (1, "$1:100")}, "mux", None,
        registered_paths={"wt-one": str(tmp_path)},
        before_discover=lambda: pytest.fail("healthy sessions have no discovery mutation"),
    ) == {}


def test_healthy_managed_mapping_adds_no_scan_probe_or_ipc(monkeypatch, tmp_path):
    from agent_worktrees import mux_link

    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(dict(
        project="project", worktree_id="one", mux_session="wt-one",
        worktree_path=str(tmp_path), session_incarnation="$1:100",
        mapping_revision=7, live=True,
    ))
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: pytest.fail("healthy managed roster must not be scanned"))
    monkeypatch.setattr(discovery, "discover_via_manager",
                        lambda *a: pytest.fail("healthy managed mux must not re-register"))
    assert discovery.discover_connected_worktrees(
        {"wt-one": (2, "$1:100")}, "mux", cache,
    ) == {}


@pytest.mark.parametrize("unavailable", ["unknown", "read-failure", "refused", "probe-failure"])
def test_unresolved_discovery_retries_after_twenty_seconds(monkeypatch, tmp_path, unavailable):
    now = [100.0]
    monkeypatch.setattr(discovery, "_now", lambda: now[0], raising=False)
    scans = []

    def records():
        scans.append(now[0])
        if unavailable == "read-failure":
            raise OSError("fixture read failed")
        return [] if unavailable == "unknown" else [("project", _record("one", tmp_path))]

    monkeypatch.setattr(discovery, "_tracked_worktrees", records)
    monkeypatch.setattr(discovery, "_session_path_matches",
                        lambda *a: unavailable != "probe-failure")
    monkeypatch.setattr(discovery, "discover_via_manager",
                        lambda *a: {"applied": False, "reason": "fixture-refusal"})
    live = {"wt-one": (1, "$1:100")}
    assert discovery.discover_connected_worktrees(live, "mux", None) == {}
    now[0] = 119.0
    assert discovery.discover_connected_worktrees(live, "mux", None) == {}
    assert scans == [100.0]
    # Unknown native enumeration must not clear the retry fence.
    assert discovery.discover_connected_worktrees(None, "mux", None) == {}
    now[0] = 120.0
    assert discovery.discover_connected_worktrees(live, "mux", None) == {}
    assert scans == [100.0, 120.0]


def test_new_native_incarnation_does_not_inherit_old_retry_delay(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_now", lambda: 100.0, raising=False)
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])
    attempts = []
    monkeypatch.setattr(discovery, "discover_via_manager", lambda payload: (
        attempts.append(payload["session_incarnation"])
        or {"applied": False, "reason": "fixture-refusal"}
    ))
    for incarnation in ("$1:100", "$2:200"):
        discovery.discover_connected_worktrees({"wt-one": (1, incarnation)}, "mux", None)
    assert attempts == ["$1:100", "$2:200"]


def test_retry_cadence_starts_after_attempt_completion(monkeypatch, tmp_path):
    now = [100.0]
    monkeypatch.setattr(discovery, "_now", lambda: now[0])
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])
    attempts = []

    def refused(payload):
        attempts.append(now[0])
        now[0] += 5.0
        return {"applied": False, "reason": "fixture-refusal"}

    monkeypatch.setattr(discovery, "discover_via_manager", refused)
    live = {"wt-one": (1, "$1:100")}
    discovery.discover_connected_worktrees(live, "mux", None)
    now[0] = 124.0
    discovery.discover_connected_worktrees(live, "mux", None)
    # An unknown attachment count is not a disconnect or a cleared retry fence.
    discovery.discover_connected_worktrees({"wt-one": (None, "$1:100")}, "mux", None)
    assert attempts == [100.0]
    now[0] = 125.0
    discovery.discover_connected_worktrees(live, "mux", None)
    assert attempts == [100.0, 125.0]


def test_new_sessions_are_fair_while_earlier_candidates_are_deferred(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_now", lambda: 100.0)
    monkeypatch.setattr(discovery, "_tracked_worktrees", lambda: [
        ("project", _record(str(i), tmp_path)) for i in range(6)
    ])
    attempts = []
    monkeypatch.setattr(discovery, "discover_via_manager", lambda payload: (
        attempts.append(payload["mux_session"]) or {"applied": False, "reason": "fixture-refusal"}
    ))
    for count in (2, 4, 6):
        live = {f"wt-{i}": (1, f"${i}:100") for i in range(count)}
        discovery.discover_connected_worktrees(live, "mux", None)
    assert attempts == [f"wt-{i}" for i in range(6)]


def test_discovery_rechecks_only_at_the_qualified_mutation_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "_tracked_worktrees",
                        lambda: [("project", _record("one", tmp_path))])
    events = []
    monkeypatch.setattr(discovery, "discover_via_manager",
                        lambda payload: events.append("admission") or None)
    monkeypatch.setattr(discovery, "_register_standalone",
                        lambda *args: events.append("standalone-registration") or True)
    discovery.discover_connected_worktrees(
        {"wt-one": (1, "$1:100")}, "mux", None,
        before_discover=lambda: events.append("governance"),
    )
    assert events == ["governance", "admission", "standalone-registration"]
