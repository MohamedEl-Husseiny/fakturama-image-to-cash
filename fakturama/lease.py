"""Process-owned desktop driver lease; no desktop imports or record changes."""

import atexit
import os
from pathlib import Path


class DriverBusyError(RuntimeError):
    """Another workflow owns the shared desktop input driver."""


def desktop_lock_path() -> Path:
    return Path(__file__).resolve().parents[1] / "evidence" / "locks" / "desktop-driver.lock"


class DesktopLease:
    """An OS lock, automatically released on process exit, including a crash.

    Every workspace shares the one keyboard and mouse, so read-only runs take the
    lease too. A leftover lock file is harmless; the OS lock is what counts.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._file = None

    def acquire(self):
        if self._file is not None:
            raise RuntimeError("This desktop lease is already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise DriverBusyError(
                "Another workflow owns the desktop driver; no UI input attempted"
            ) from exc
        self._file = stream
        atexit.register(self.release)
        return self

    def release(self):
        if self._file is None:
            return
        stream, self._file = self._file, None
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            atexit.unregister(self.release)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
