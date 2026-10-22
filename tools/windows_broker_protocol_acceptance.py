"""Production protocol in a synthetic, explicitly authorized disposable fixture.

Copies source into an owned immutable fixture directory; installs no packages,
updates no host runtime and never registers outside a hosted Windows CI runner.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def admit(root: Path, consent: bool) -> None:
    temporary = os.environ.get("RUNNER_TEMP")
    if (
        not consent or os.name != "nt"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not temporary or not root.resolve().is_relative_to(Path(temporary).resolve())
        or not root.name.startswith("ce-broker-probe-")
    ):
        raise RuntimeError("protocol fixture requires explicit ephemeral Windows CI consent")


def imports(package_root: Path) -> None:
    sys.path.insert(0, str(package_root))


def caller(package_root: Path, root: Path) -> int:
    admit(root, True)
    imports(package_root)
    from agent_procutil.windows_task_broker import spawn

    if sys.stdin.readline() != "launch\n":
        return 2
    child = spawn(
        [
            str(Path(sys.base_prefix) / "python.exe"), "-I", str(root / "resident.py"),
            "argument with spaces", "", "\u65e5\n&",
        ],
        cwd=str(root), env={
            name: value for name, value in os.environ.items()
            if name in {"SystemRoot", "TEMP", "TMP", "PATH", "BROKER_FIXTURE_VALUE"}
        },
        creationflags=0x08000000,
        log_path=str(root / "resident.log"), stderr_log_path=str(root / "resident-error.log"),
    )
    offer_path = root / "caller-offer.json"
    temporary = root / "caller-offer.tmp"
    temporary.write_text(json.dumps(child.offer()), encoding="utf-8")
    temporary.replace(offer_path)
    # The parent duplicates actual child custody before closing every caller Job.
    sys.stdin.readline()
    child.close()
    return 0


def inspect(root: Path) -> dict[str, object]:
    source = Path(__file__).resolve().parents[1]
    packages = root / "immutable" / "site-packages"
    packages.mkdir(parents=True)
    for library, package in (("agent-procutil", "agent_procutil"), ("zdd", "zdd")):
        shutil.copytree(source / "libs" / library / "src" / package, packages / package)
    imports(packages)
    import agent_procutil as pu
    from agent_procutil.daemon_broker import _outside_job
    from agent_procutil.windows_broker_native import NativeChild, _duplicate
    from agent_procutil.windows_task_broker import (
        CONFIG_ENV,
        OWNER_ENV,
        Registration,
        reconcile,
        task_operation,
    )
    from zdd.private_protocol import Deadline
    from zdd.private_windows_native import native

    pythonw = Path(sys.base_prefix) / "pythonw.exe"
    config = Registration(
        "synthetic-protocol-" + root.name, str(root), str(pythonw),
        str(packages / "agent_procutil" / "windows_broker_entry.py"), native().current_sid(),
    )
    (root / "resident.py").write_text(
        "import ctypes,json,os,sys,time\n"
        "k=ctypes.WinDLL('kernel32');u=ctypes.WinDLL('user32')\n"
        "k.GetConsoleWindow.restype=ctypes.c_void_p\n"
        "u.IsWindowVisible.argtypes=[ctypes.c_void_p]\n"
        "print(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),"
        "'value':os.environ.get('BROKER_FIXTURE_VALUE'),"
        "'headless':not bool(u.IsWindowVisible(k.GetConsoleWindow()))}),flush=True)\n"
        "print('fixture-error',file=sys.stderr,flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    result: dict[str, object] = {
        "scope": "Production native protocol; synthetic disposable registration, not deployment",
        "supported": False, "cleanup_confirmed": False, "cycles": [],
    }
    callers = []
    children = []
    jobs = []
    adopted = False
    try:
        reconcile(config)
        adopted = True
        reconcile(config)
        first_status = task_operation("status", config, Deadline(10))
        if first_status.get("configured") is not True:
            raise RuntimeError("fixture adoption was not configured")
        for cycle in range(2):
            offer_path = root / "caller-offer.json"
            offer_path.unlink(missing_ok=True)
            env = {
                name: value for name, value in os.environ.items()
                if name in {
                    "SystemRoot", "TEMP", "TMP", "PATH", "RUNNER_TEMP",
                    "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT",
                }
            }
            env.update({
                CONFIG_ENV: str(config.config_path), OWNER_ENV: config.installation,
                "BROKER_FIXTURE_VALUE": "live-only-synthetic\n&",
            })
            process, outer = pu.spawn_sync_in_kill_on_close_job(
                [
                    str(Path(sys.base_prefix) / "python.exe"), "-I", str(Path(__file__).resolve()),
                    "caller", str(packages), str(root),
                ],
                require_job=True, allow_breakaway=False, env=env, cwd=root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", creationflags=0x08000000,
            )
            callers.append(process)
            if outer is None:
                raise RuntimeError("fixture caller cleanup Job unavailable")
            jobs.append(outer)
            api = pu._kernel32()
            pu._configure_job_api(api)
            inner = api.CreateJobObjectW(None, None)
            if not inner:
                raise ctypes.WinError(ctypes.get_last_error())
            inner_owner = pu.JobHandle(inner)
            jobs.append(inner_owner)
            limits = pu._build_job_extended_limit_info()()
            limits.BasicLimitInformation.LimitFlags = pu._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(
                inner, pu._JobObjectExtendedLimitInformation, ctypes.byref(limits),
                ctypes.sizeof(limits),
            ) or not api.AssignProcessToJobObject(inner, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            process.stdin.write("launch\n")
            process.stdin.flush()
            deadline = time.monotonic() + 40
            while not offer_path.exists():
                if process.poll() is not None:
                    raise RuntimeError(
                        "fixture caller failed before custody transfer: " + process.stderr.read(),
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("fixture child custody deadline")
                time.sleep(.025)
            offer = json.loads(offer_path.read_text(encoding="utf-8"))
            handle = _duplicate(int(process._handle), offer["source_handle"])
            child = NativeChild.from_handle(handle, offer["pid"], ["synthetic-resident"])
            children.append(child)
            actual = native().identity(handle, child.pid)
            if (
                native().k.GetProcessId(handle) != child.pid
                or actual.start_time != offer["birth"] or not _outside_job(child)
            ):
                raise RuntimeError("fixture actual child identity or lifetime refused")
            inner_owner.terminate_and_wait(5)
            outer.terminate_and_wait(5)
            process.wait(timeout=5)
            for _ in range(2):
                try:
                    child.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    continue
                raise RuntimeError("resident died with fixture caller cleanup")
            observations = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                observations = (root / "resident.log").read_text(encoding="utf-8").splitlines()
                if len(observations) >= cycle + 1:
                    break
                time.sleep(.025)
            observed = json.loads(observations[cycle])
            if observed != {
                "argv": ["argument with spaces", "", "\u65e5\n&"],
                "cwd": str(root), "value": "live-only-synthetic\n&", "headless": True,
            }:
                raise RuntimeError("fixture exact resident contract mismatch")
            if "fixture-error" not in (root / "resident-error.log").read_text(encoding="utf-8"):
                raise RuntimeError("fixture stderr destination mismatch")
            child.retire(Deadline(5))
            child.close()
            result["cycles"].append({
                "cycle": cycle + 1, "actual_child_pid": child.pid,
                "finite_broker_exit_proved": True, "all_caller_jobs_closed": True,
                "exact_contract": True, "actual_child_cleanup_confirmed": True,
            })
        result["supported"] = True
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        result["failure_type"] = type(error).__name__
        result["failure"] = str(error)
    finally:
        cleanup = True
        for child in children:
            if child._handle is not None:
                try:
                    child.retire(Deadline(5))
                    child.close()
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    cleanup = False
        for job in reversed(jobs):
            if job.handle is not None:
                try:
                    job.terminate_and_wait(5)
                except (OSError, RuntimeError):
                    cleanup = False
        for process in callers:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup = False
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if adopted:
            try:
                task_operation("uninstall", config, Deadline(15))
            except (OSError, RuntimeError, subprocess.SubprocessError):
                cleanup = False
        result["cleanup_confirmed"] = cleanup and adopted
        (root / "protocol-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8",
        )
    return result


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "caller":
        return caller(Path(args[1]), Path(args[2]))
    if len(args) != 2 or args[0] != "--ephemeral-ci-consent":
        raise RuntimeError("protocol fixture requires explicit ephemeral Windows CI consent")
    root = Path(args[1])
    admit(root, True)
    result = inspect(root)
    print(json.dumps(result))
    return 0 if result["supported"] and result["cleanup_confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
