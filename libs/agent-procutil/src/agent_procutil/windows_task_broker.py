"""Explicit consumer-owned Scheduled Task adoption and finite launch activation.

Only the owning canonical installer calls reconcile/remove. The spawn path
reads an already adopted registration and never installs, repairs or updates it.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

from zdd.private_protocol import Deadline, PeerIdentity
from zdd.private_windows import WindowsListener, connect
from zdd.private_windows_native import native

from . import _validated_daemon_cwd
from .owned_process import run_caller_owned
from .windows_broker_native import NativeBrokerPeer, NativeChild, stage
from .windows_broker_protocol import LaunchRequest, _path, _refuse, launch, retry_cleanup, serve

CONFIG_ENV = "AGENT_PROCUTIL_WINDOWS_BROKER_CONFIG"
OWNER_ENV = "AGENT_PROCUTIL_WINDOWS_BROKER_OWNER"
_SCHEMA = "agent-procutil.windows-launch-broker"
_NONCE = re.compile(r"[0-9a-f]{64}")
_ACTIVATION_PHASES = frozenset({
    "input", "principal", "connect", "folder", "lookup", "validated",
    "run-requested", "run-returned", "engine-ready", "revalidated",
    "process", "birth", "response",
})


@dataclass(frozen=True)
class Registration:
    """Non-secret installation metadata, never child argv or credentials."""

    installation: str
    root: str
    pythonw: str
    entrypoint: str
    principal: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.installation, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/@+-]{1,256}", self.installation)
            or not isinstance(self.principal, str)
            or not re.fullmatch(r"S-1-[0-9-]{1,180}", self.principal)
        ):
            raise _refuse("installation identity refused")
        for path in (self.root, self.pythonw, self.entrypoint):
            _path(path)
            if path.startswith("\\\\") or '"' in path:
                raise _refuse("installation path refused")
        if (
            ntpath.basename(self.pythonw).lower() != "pythonw.exe"
            or ntpath.basename(self.entrypoint) != "windows_broker_entry.py"
        ):
            raise _refuse("installation path refused")

    @property
    def key(self) -> str:
        return hashlib.sha256(
            (self.installation + "\0" + self.principal).encode("utf-8"),
        ).hexdigest()

    @property
    def config_path(self) -> Path:
        return Path(ntpath.join(self.root, "windows-launch-broker.json"))

    @property
    def owner(self) -> str:
        return _SCHEMA + ":" + self.key

    def task(self) -> dict[str, object]:
        # Scheduler substitutions contain only nonce/PID/birth public metadata.
        args = subprocess.list2cmdline([
            "-I", self.entrypoint, str(self.config_path), "$(Arg0)", "$(Arg1)", "$(Arg2)",
        ])
        return {
            "owner": self.owner, "task_name": "CE-LaunchBroker-" + self.key,
            "principal": self.principal, "pythonw": self.pythonw,
            "arguments": args, "cwd": self.root,
        }

    def frame(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA, "version": 1, "installation": self.installation,
            "root": self.root, "pythonw": self.pythonw, "entrypoint": self.entrypoint,
            "principal": self.principal,
        }

    def validate_files(self) -> None:
        _validated_daemon_cwd(self.root)
        root = Path(self.root).resolve()
        for path in (Path(self.pythonw), Path(self.entrypoint), root):
            if path.resolve() != path.absolute() or any(
                parent.is_symlink() or (parent / ".git").exists()
                for parent in (path, *path.parents)
            ):
                raise _refuse("transient installation refused")
        if (
            not Path(self.pythonw).is_file() or not Path(self.entrypoint).is_file()
            or not Path(self.entrypoint).is_relative_to(root)
            or native().current_sid() != self.principal
        ):
            raise _refuse("installation prerequisite unavailable")


def load(path: Path, installation: str) -> Registration:
    """Read bounded owned metadata; an ambient pointer is not installation identity."""
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            raise _refuse("adoption required")
        with path.open("rb") as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise _refuse("adoption required")
        value = json.loads(raw)
        if type(value) is not dict or set(value) != {
            "schema", "version", "installation", "root", "pythonw", "entrypoint", "principal",
        } or (
            value["schema"] != _SCHEMA
            or type(value["version"]) is not int or value["version"] != 1
        ):
            raise _refuse("adoption required")
        config = Registration(
            value["installation"], value["root"], value["pythonw"],
            value["entrypoint"], value["principal"],
        )
        if config.installation != installation or path.absolute() != config.config_path:
            raise _refuse("installation identity refused")
        config.validate_files()
        return config
    except (OSError, ValueError, TypeError, KeyError):
        raise _refuse("adoption required") from None


def task_operation(
    action: str, config: Registration, deadline: Deadline, **metadata: object,
) -> dict[str, object]:
    """Bound the native scheduler adapter; stdin contains no launch secrets."""
    if action not in {"install", "update", "activate", "status", "stop", "uninstall"}:
        raise _refuse("task operation refused")
    if os.name != "nt":
        raise _refuse("Windows prerequisite unavailable")
    config.validate_files()
    powershell = Path(os.environ["SystemRoot"]) / (
        r"System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    try:
        result = run_caller_owned(
            [
                str(powershell), "-NoProfile", "-NonInteractive", "-File",
                str(Path(__file__).with_name("windows_broker_task.ps1")), "-Action", action,
            ],
            timeout=deadline.remaining(), max_stdout_bytes=65536, max_stderr_bytes=65536,
            input_text=json.dumps({"config": config.task(), **metadata}, ensure_ascii=True),
        )
    except subprocess.TimeoutExpired as error:
        phase = "unobserved"
        for line in (error.stderr or b"").splitlines():
            for candidate in _ACTIVATION_PHASES:
                if line == ("broker-phase:" + candidate).encode("ascii"):
                    phase = candidate
                    break
        raise _refuse("task " + action + " timed out (phase=" + phase + ")") from None
    if result.returncode != 0:
        reason = "\n".join(
            line for line in result.stderr.splitlines()
            if line not in {"broker-phase:" + phase for phase in _ACTIVATION_PHASES}
        ).strip()
        allowed = {
            "broker-registration-owner-refused", "broker-registration-action-refused",
            "broker-registration-principal-refused", "broker-registration-policy-refused",
            "broker-active-transactions-remain", "broker-principal-refused",
            "broker-adoption-required", "broker-admission-disabled", "broker-activation-deadline",
            "broker-registration-rollback-unconfirmed",
        }
        reason = reason if reason in allowed else "scheduler-operation-failed"
        raise _refuse("task " + action + " refused (" + reason + ")")
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise _refuse("task response refused") from None
    if type(value) is not dict:
        raise _refuse("task response refused")
    deadline.remaining()
    return value


def _publish(config: Registration) -> None:
    # The installer already owns the private root and its execution transaction.
    path = config.config_path
    temporary = path.with_name(path.name + "." + secrets.token_hex(16) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(config.frame(), stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile(config: Registration, *, previous: Registration | None = None) -> None:
    """Installer integration after immutable-slot qualification, under its driver.

    No dependency installation or independent mutation lock is introduced here.
    The consumer supplies its validated installation identity and private root.
    A replacement is not activated while the old finite task accepts work.
    """
    config.validate_files()
    deadline = Deadline(30)
    if previous is not None and (
        previous.installation != config.installation
        or previous.principal != config.principal or previous.root != config.root
    ):
        raise _refuse("installation transition refused")
    action = "install" if previous is None else "update"
    task_operation(
        action, config, deadline, **({} if previous is None else {"previous": previous.task()}),
    )
    try:
        _publish(config)
    except BaseException:
        # Roll back only the exact registration this installer just changed.
        if previous is None:
            task_operation("uninstall", config, Deadline(15))
        else:
            task_operation("update", previous, Deadline(15), previous=config.task())
        raise


def pipe_path(config: Registration, nonce: str) -> str:
    if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
        raise _refuse("rendezvous refused")
    return "\\\\.\\pipe\\ce-launch-" + config.key + "-" + nonce


def spawn(
    command: list[str], *, cwd: str, env: dict[str, str], creationflags: int,
    log_path: str, stderr_log_path: str | None,
) -> NativeChild:
    """Use an adopted broker; never provision or retry an ambiguous transaction."""
    if os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        raise _refuse("contained activation refused")
    configured, owner = os.environ.get(CONFIG_ENV), os.environ.get(OWNER_ENV)
    if not configured or not owner:
        raise _refuse("adoption required")
    config = load(Path(configured), owner)
    request = LaunchRequest.parse({
        "argv": command, "cwd": cwd, "env": env, "creationflags": creationflags,
        "log_path": log_path, "stderr_log_path": stderr_log_path,
    })
    deadline = Deadline(30)
    retry_cleanup(deadline)
    api = native()
    caller = api.identity(api.k.GetCurrentProcess(), os.getpid())
    nonce = secrets.token_hex(32)
    activated = task_operation(
        "activate", config, deadline, nonce=nonce,
        client_pid=caller.pid, client_birth=caller.start_time,
    )
    if (
        set(activated) != {"instance", "pid", "birth", "principal"}
        or not isinstance(activated["instance"], str)
        or re.fullmatch(
            r"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}",
            activated["instance"],
        ) is None
    ):
        raise _refuse("activation identity refused")
    expected = PeerIdentity(activated["pid"], activated["birth"], activated["principal"])
    if expected.principal != caller.principal:
        raise _refuse("activation identity refused")
    peer = NativeBrokerPeer(expected)
    channel = None
    try:
        if os.path.normcase(api.process_image(peer.process._handle)) != os.path.normcase(
            config.pythonw,
        ):
            raise _refuse("activation image refused")
        channel = connect(pipe_path(config, nonce), expected, deadline)
        return launch(channel, peer, request, deadline)
    finally:
        try:
            if channel is not None:
                channel.close()
        finally:
            peer.close()


def serve_main(args: list[str]) -> int:
    """One native-authenticated request, then exit after custody transfer."""
    if len(args) != 4 or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        return 2
    listener = None
    try:
        path, nonce, pid, birth = args
        # The installer-owned action is scoped to this exact config location.
        with Path(path).open("rb") as stream:
            metadata = json.loads(stream.read(16385))
        config = load(Path(path), metadata["installation"])
        actual = Path(__file__).with_name("windows_broker_entry.py").resolve()
        if actual != Path(config.entrypoint):
            raise _refuse("activation generation refused")
        if not pid.isdecimal() or not birth.isdecimal():
            raise _refuse("client identity refused")
        expected = PeerIdentity(int(pid), birth, config.principal)
        deadline = Deadline(30)
        listener = WindowsListener(pipe_path(config, nonce))
        channel = listener.accept(deadline, expected)
        serve(channel, stage, deadline)
        return 0
    except Exception:
        # No exception value, request field or credential reaches a log/console.
        # Without successful finite completion, never claim broker-side cleanup.
        return 2
    finally:
        if listener is not None:
            listener.close()
