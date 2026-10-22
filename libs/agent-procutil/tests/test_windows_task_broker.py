"""Adoption never becomes a cold installer on the ordinary spawn path."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from zdd.private_protocol import PeerIdentity

from agent_procutil import DaemonSpawnError
from agent_procutil import windows_task_broker as task


def registration(**overrides):
    return task.Registration(**{
        "installation": "test-marketplace/plugin",
        "root": r"C:\runtime\broker",
        "pythonw": r"C:\managed-python\pythonw.exe",
        "entrypoint": r"C:\runtime\broker\immutable\agent_procutil\windows_broker_entry.py",
        "principal": "S-1-5-21-123",
        **overrides,
    })


def test_registration_names_are_installation_and_user_scoped():
    config = registration()
    other_installation = registration(installation="other/plugin")
    assert config.task()["task_name"] != other_installation.task()["task_name"]
    assert config.task()["task_name"] != registration(principal="S-1-5-21-456").task()["task_name"]
    assert len(config.key) == 64
    assert "$(Arg0)" in config.task()["arguments"]
    assert "$(Arg1)" in config.task()["arguments"]
    assert "$(Arg2)" in config.task()["arguments"]
    assert set(config.frame()) == {
        "schema", "version", "installation", "root", "pythonw", "entrypoint", "principal",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"installation": ""}, {"installation": "unsafe\nidentity"},
        {"principal": "not-a-sid"}, {"principal": False},
        {"root": r"C:relative"}, {"root": r"\\server\shared"},
        {"pythonw": r'C:\unsafe"\pythonw.exe'},
        {"pythonw": r"C:\runtime\python.exe"},
        {"entrypoint": r"C:\runtime\other.py"},
    ],
)
def test_unsafe_registration_is_refused(overrides):
    with pytest.raises(DaemonSpawnError):
        registration(**overrides)


def test_contained_spawn_does_not_read_adoption_or_activate(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    load, operation = Mock(), Mock()
    monkeypatch.setattr(task, "load", load)
    monkeypatch.setattr(task, "task_operation", operation)
    with pytest.raises(DaemonSpawnError, match="contained"):
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    load.assert_not_called()
    operation.assert_not_called()


@pytest.mark.parametrize("missing", [task.CONFIG_ENV, task.OWNER_ENV])
def test_missing_adoption_never_installs(monkeypatch, missing):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setenv(task.CONFIG_ENV, r"C:\state\windows-launch-broker.json")
    monkeypatch.setenv(task.OWNER_ENV, "test/plugin")
    monkeypatch.delenv(missing)
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    with pytest.raises(DaemonSpawnError, match="adoption required"):
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    operation.assert_not_called()


def test_installer_publish_failure_rolls_back_only_exact_registration(monkeypatch):
    config = registration()
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    monkeypatch.setattr(task, "_publish", Mock(side_effect=OSError("fixture publication failure")))
    with pytest.raises(OSError, match="publication"):
        task.reconcile(config)
    assert [call.args[0] for call in operation.call_args_list] == ["install", "uninstall"]
    assert all(call.args[1] is config for call in operation.call_args_list)


def test_installer_update_publish_failure_restores_previous_generation(monkeypatch):
    old = registration()
    new = registration(
        entrypoint=r"C:\runtime\broker\next\agent_procutil\windows_broker_entry.py",
    )
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    monkeypatch.setattr(task, "_publish", Mock(side_effect=OSError("fixture failure")))
    with pytest.raises(OSError, match="fixture"):
        task.reconcile(new, previous=old)
    first, rollback = operation.call_args_list
    assert first.args[:2] == ("update", new)
    assert first.kwargs == {"previous": old.task()}
    assert rollback.args[:2] == ("update", old)
    assert rollback.kwargs == {"previous": new.task()}


def test_installer_refuses_foreign_generation_before_native_operation(monkeypatch):
    monkeypatch.setattr(task.Registration, "validate_files", lambda config: None)
    operation = Mock()
    monkeypatch.setattr(task, "task_operation", operation)
    with pytest.raises(DaemonSpawnError, match="transition"):
        task.reconcile(registration(), previous=registration(installation="foreign/plugin"))
    operation.assert_not_called()


def test_malformed_adoption_metadata_never_reaches_scheduler(tmp_path):
    path = tmp_path / "windows-launch-broker.json"
    path.write_text(json.dumps({"schema": "wrong", "version": 1}), encoding="utf-8")
    with pytest.raises(DaemonSpawnError, match="adoption required"):
        task.load(path, "test/plugin")
    path.write_bytes(b"x" * 16385)
    with pytest.raises(DaemonSpawnError, match="adoption required"):
        task.load(path, "test/plugin")


def test_rendezvous_is_bounded_local_and_unique():
    config = registration()
    address = task.pipe_path(config, "a" * 64)
    assert address.startswith("\\\\.\\pipe\\")
    assert len(address) <= 256
    assert address != task.pipe_path(config, "b" * 64)
    assert address != task.pipe_path(registration(installation="other/plugin"), "a" * 64)
    with pytest.raises(DaemonSpawnError, match="rendezvous"):
        task.pipe_path(config, "a\\b")


def spawn_fixture(monkeypatch, activation=None):
    config = registration()
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setenv(task.CONFIG_ENV, str(config.config_path))
    monkeypatch.setenv(task.OWNER_ENV, config.installation)
    monkeypatch.setattr(task, "load", Mock(return_value=config))
    api = Mock()
    api.identity.return_value = PeerIdentity(91001, "300", config.principal)
    api.process_image.return_value = config.pythonw
    monkeypatch.setattr(task, "native", lambda: api)
    operation = Mock(return_value=activation or {
        "instance": "{12345678-1234-1234-1234-123456789012}",
        "pid": 92001, "birth": "400", "principal": config.principal,
    })
    monkeypatch.setattr(task, "task_operation", operation)
    return config, api, operation


def test_spawn_authenticates_native_instance_before_live_request(monkeypatch):
    config, _, operation = spawn_fixture(monkeypatch)
    peer = Mock()
    constructor, connect, launch = Mock(return_value=peer), Mock(), Mock()
    monkeypatch.setattr(task, "NativeBrokerPeer", constructor)
    monkeypatch.setattr(task, "connect", connect)
    monkeypatch.setattr(task, "launch", launch)
    value = task.spawn(
        ["python", "live-only-fixture"], cwd=r"C:\state",
        env={"VALUE": "live-only-fixture"}, creationflags=0x08000000,
        log_path=r"C:\state\out.log", stderr_log_path=None,
    )
    assert value is launch.return_value
    assert [call.args[0] for call in operation.call_args_list] == ["activate"]
    assert "live-only-fixture" not in repr(operation.call_args_list)
    expected = PeerIdentity(92001, "400", config.principal)
    constructor.assert_called_once_with(expected)
    assert connect.call_args.args[1] == expected
    assert launch.call_args.args[1] is peer
    assert launch.call_args.args[2].env == {"VALUE": "live-only-fixture"}
    peer.close.assert_called_once()


def test_engine_pipe_mismatch_sends_no_launch_frame(monkeypatch):
    spawn_fixture(monkeypatch)
    peer = Mock()
    monkeypatch.setattr(task, "NativeBrokerPeer", Mock(return_value=peer))
    monkeypatch.setattr(task, "connect", Mock(side_effect=RuntimeError("native PID mismatch")))
    launch = Mock()
    monkeypatch.setattr(task, "launch", launch)
    with pytest.raises(RuntimeError, match="mismatch"):
        task.spawn(
            ["python"], cwd=r"C:\state", env={}, creationflags=0x08000000,
            log_path=r"C:\state\out.log", stderr_log_path=None,
        )
    launch.assert_not_called()
    peer.close.assert_called_once()


def test_serve_rejects_containment_before_native_actions(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    listener = Mock()
    monkeypatch.setattr(task, "WindowsListener", listener)
    assert task.serve_main(["config", "nonce", "pid", "birth"]) == 2
    listener.assert_not_called()


def test_packaged_scheduler_adapter_is_declared():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["project"]["dependencies"] == []
    assert "windows_broker_task.ps1" in manifest["tool"]["setuptools"]["package-data"][
        "agent_procutil"
    ]
    assert manifest["project"]["optional-dependencies"]["windows-broker"] == [
        "agent-zdd>=0.1.0.dev7; sys_platform == 'win32'",
    ]
