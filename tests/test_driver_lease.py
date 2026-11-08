"""Real OS/process lease tests; no Fakturama windows or records are touched."""

from pathlib import Path
import subprocess
import sys

import pytest

from fakturama.lease import DesktopLease, DriverBusyError


def child_attempt(path):
    code = (
        "import sys; from fakturama.lease import DesktopLease, DriverBusyError; "
        "lease=DesktopLease(sys.argv[1]);\n"
        "try: lease.acquire()\n"
        "except DriverBusyError: sys.exit(7)\n"
        "lease.release()\n"
    )
    return subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, timeout=15)


def test_another_process_is_excluded_then_can_acquire_after_release(tmp_path):
    path = tmp_path / "driver.lock"
    with DesktopLease(path):
        result = child_attempt(path)
        assert result.returncode == 7, result.stderr
    result = child_attempt(path)
    assert result.returncode == 0, result.stderr


def test_process_crash_releases_os_lock_without_deleting_stale_file(tmp_path):
    path = tmp_path / "driver.lock"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys; from fakturama.lease import DesktopLease; "
            "lease=DesktopLease(sys.argv[1]).acquire(); os._exit(0)",
            str(path),
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert path.exists()
    with DesktopLease(path):
        pass


def test_busy_driver_prevents_runner_initialization(tmp_path, monkeypatch):
    from fakturama_full_workflow import Runner

    path = tmp_path / "driver.lock"
    monkeypatch.setattr("fakturama_full_workflow.desktop_lock_path", lambda: path)
    monkeypatch.setattr(
        Runner, "_initialize", lambda *a, **k: pytest.fail("must not connect to desktop")
    )
    with DesktopLease(path):
        with pytest.raises(DriverBusyError):
            Runner(None, tmp_path / "evidence")


def test_failed_runner_initialization_releases_driver(tmp_path, monkeypatch):
    from fakturama_full_workflow import Runner

    path = tmp_path / "driver.lock"
    monkeypatch.setattr("fakturama_full_workflow.desktop_lock_path", lambda: path)

    def fail(*args, **kwargs):
        raise ValueError("initialization failure")

    monkeypatch.setattr(Runner, "_initialize", fail)
    with pytest.raises(ValueError, match="initialization failure"):
        Runner(None, tmp_path / "evidence")
    with DesktopLease(path):
        pass
