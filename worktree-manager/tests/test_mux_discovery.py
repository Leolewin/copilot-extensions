"""Native discovery retains revision, tombstone and incarnation fences."""

import subprocess

import pytest

from worktree_manager import mux_discovery
from worktree_manager import mux_daemon
from worktree_manager.mux_mapping_registry import MuxMappingRegistry


def _payload(**overrides):
    result = dict(project="project", worktree_id="one", worktree_path="workspace",
                  mux_session="wt-one", mux_bin="mux", session_incarnation="$1:100",
                  attached_clients=1)
    result.update(overrides)
    return result


def _probe(monkeypatch, stdout="wt-one\t1\t$1:100\tworkspace\n", code=0):
    monkeypatch.setattr(mux_discovery, "run_mux_child",
                        lambda *a, **kw: subprocess.CompletedProcess(a, code, stdout=stdout))


def test_connected_mapping_is_admitted_and_survives_daemon_restart(monkeypatch, tmp_path):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    first = mux_discovery.discover_mapping(registry, _payload())
    assert first["applied"]
    assert first["entry"]["mapping_revision"] == 1
    second = mux_discovery.discover_mapping(MuxMappingRegistry(registry._path), _payload())
    assert second["applied"]
    assert second["entry"]["mapping_revision"] == 1


@pytest.mark.parametrize("stdout,code", [
    ("wt-one\t1\t$2:200\tworkspace\n", 0),
    ("wt-one\t0\t$1:100\tworkspace\n", 0), ("", 1), ("bad", 0),
    ("wt-one\t1\t$1:100\tforeign\n", 0),
])
def test_failed_unknown_or_stale_native_probe_does_not_register(monkeypatch, tmp_path, stdout, code):
    _probe(monkeypatch, stdout, code)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert registry.snapshot() == {}


@pytest.mark.parametrize("live", [True, False])
def test_discovery_cannot_replace_incumbent_or_tombstone(monkeypatch, tmp_path, live):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(session_incarnation="launcher-token"),
                           live=live, mapping_revision=9))
    before = registry.snapshot()
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert registry.snapshot() == before


def test_foreign_name_cannot_be_admitted(monkeypatch, tmp_path):
    _probe(monkeypatch, "shell\t1\t$1:100\tworkspace\n")
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert not mux_discovery.discover_mapping(
        registry, _payload(mux_session="shell"),
    )["applied"]


def test_status_compute_discovers_then_presents_via_existing_owner(monkeypatch, tmp_path):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    compute = mux_daemon.build_compute(registry)
    admitted = compute(mux_discovery.KIND, _payload())
    assert admitted["applied"]
    assert admitted["entry"]["attached_clients"] == 1
    writes = []
    monkeypatch.setattr(mux_daemon, "_mux_session_alive", lambda *a: True)
    monkeypatch.setattr(mux_daemon, "apply_status_options",
                        lambda entry, values: writes.append((entry, values)) or True)
    monkeypatch.setattr(mux_daemon.mux_daemon_live, "publish_live_observation",
                        lambda *a, **kw: {"applied": True})
    status = dict(project="project", worktree_id="one", values={"@aw_seg": "status"},
                  rendered_at="2026-10-22T12:00:00Z", mapping_revision=1,
                  mux_session="wt-one", session_incarnation="$1:100")
    assert compute(mux_daemon.KIND, status)["applied"]
    assert writes[0][1] == {"@aw_seg": "status"}
    # A same-name replacement is not the observed incarnation.
    _probe(monkeypatch, "wt-one\t1\t$2:200\tworkspace\n")
    assert compute(mux_daemon.KIND, status)["reason"] == "stale-incarnation"
    assert len(writes) == 1
    assert registry.get("project", "one")["live"]


@pytest.mark.parametrize("fence", [
    {"mapping_revision": 0}, {"mux_session": "other"},
    {"session_incarnation": "old"},
])
def test_status_compute_refuses_stale_sender_fences(monkeypatch, tmp_path, fence):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert mux_discovery.discover_mapping(registry, _payload())["applied"]
    monkeypatch.setattr(mux_daemon, "apply_status_options",
                        lambda *a: pytest.fail("stale mapping must not paint"))
    compute = mux_daemon.build_compute(registry)
    status = dict(project="project", worktree_id="one", values={"@aw_seg": "stale"},
                  rendered_at="2026-10-22T12:00:00Z", **fence)
    assert compute(mux_daemon.KIND, status)["reason"] == "stale-mapping"


def test_enumeration_failure_preserves_prior_attached_count(monkeypatch, tmp_path):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert mux_discovery.discover_mapping(registry, _payload())["applied"]
    before = registry.snapshot()
    _probe(monkeypatch, "", 1)
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert registry.snapshot() == before


def test_discovery_rechecks_incumbent_after_native_probe(monkeypatch, tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mapping.json")

    def replaced_during_probe(*a, **kw):
        registry.register(dict(_payload(session_incarnation="new-owner"),
                               mapping_revision=7))
        return subprocess.CompletedProcess(a, 0, stdout="wt-one\t1\t$1:100\tworkspace\n")

    monkeypatch.setattr(mux_discovery, "run_mux_child", replaced_during_probe)
    assert mux_discovery.discover_mapping(registry, _payload())["reason"] == "mapping-changed"
    assert registry.get("project", "one")["mapping_revision"] == 7


def test_discovery_respects_draining_and_tracks_handler_lifetime(monkeypatch, tmp_path):
    from types import SimpleNamespace

    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    assert mux_discovery.handle_discovery(
        registry, _payload(), SimpleNamespace(accepting_requests=lambda: False),
    )["reason"] == "draining"
    events = []
    tracker = SimpleNamespace(enter=lambda: events.append("enter"),
                              exit=lambda: events.append("exit"))
    _probe(monkeypatch)
    assert mux_discovery.handle_discovery(registry, _payload(), None, tracker)["applied"]
    assert events == ["enter", "exit"]


@pytest.mark.parametrize("live", [True, False])
def test_fresh_native_incarnation_recovers_old_mapping(monkeypatch, tmp_path, live):
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(), mapping_revision=9, live=live,
                           last_status_rendered_at="2026-10-22T12:00:00Z"))
    _probe(monkeypatch, "wt-one\t1\t$2:200\tworkspace\n")
    result = mux_discovery.discover_mapping(registry, _payload(session_incarnation="$2:200"))
    assert result["applied"]
    assert result["entry"]["mapping_revision"] == 10
    assert result["entry"]["session_incarnation"] == "$2:200"
    assert result["entry"]["last_status_rendered_at"] is None
    assert registry.remove("project", "one", session_incarnation="$1:100")["reason"] == "session_mismatch"
    assert registry.get("project", "one")["live"]


def test_same_incarnation_tombstone_is_not_resurrected(monkeypatch, tmp_path):
    _probe(monkeypatch)
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(), mapping_revision=9, live=False))
    before = registry.snapshot()
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert registry.snapshot() == before


def test_fresh_admission_cannot_replace_concurrent_registration(monkeypatch, tmp_path):
    registry = MuxMappingRegistry(tmp_path / "mapping.json")
    registry.register(dict(_payload(), mapping_revision=9, live=False))

    def replaced_during_probe(*args, **kwargs):
        registry.register(dict(_payload(session_incarnation="$3:300"),
                               mapping_revision=12))
        return subprocess.CompletedProcess(args, 0, stdout="wt-one\t1\t$2:200\tworkspace\n")

    monkeypatch.setattr(mux_discovery, "run_mux_child", replaced_during_probe)
    assert not mux_discovery.discover_mapping(
        registry, _payload(session_incarnation="$2:200"),
    )["applied"]
    assert registry.get("project", "one")["mapping_revision"] == 12
    assert registry.get("project", "one")["session_incarnation"] == "$3:300"


@pytest.mark.parametrize("contents", ["[invalid-json", '{"unexpected": "registry"}'])
def test_unknown_registry_state_is_not_an_absent_mapping(monkeypatch, tmp_path, contents):
    _probe(monkeypatch)
    path = tmp_path / "mapping.json"
    path.write_text(contents, encoding="utf-8")
    registry = MuxMappingRegistry(path)
    assert not mux_discovery.discover_mapping(registry, _payload())["applied"]
    assert path.read_text(encoding="utf-8") == contents
