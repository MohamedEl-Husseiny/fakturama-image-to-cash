"""Desktop-independent, atomic execution evidence storage."""

from datetime import datetime
import json
import logging
from pathlib import Path
import time
from typing import Any


class ExecutionJournal:
    """One run's event log; evidence is separate from recovery identity hints."""

    def __init__(self, directory: str | Path, *, logger: logging.Logger | None = None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "execution_report.json"
        if self.path.exists():
            raise FileExistsError(
                "Execution report already exists; choose a new evidence directory"
            )
        self.events: list[dict[str, Any]] = []
        self.logger = logger or logging.getLogger("image_to_cash")

    def record(self, stage: str, **data: Any) -> dict[str, Any]:
        event = {"timestamp": datetime.now().isoformat(timespec="seconds"), "stage": stage, **data}
        updated = [*self.events, event]
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(updated, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        self._replace(temporary)
        self.events.append(event)
        self.logger.info("%s %s", stage, data)
        return event

    def _replace(self, temporary: Path, attempts: int = 20, pause: float = 0.1) -> None:
        """Atomic rename with a short retry: on Windows a virus scanner, indexer or
        preview pane can hold the just-written report open for a moment, and that
        transient lock must not abort a run."""
        for attempt in range(attempts):
            try:
                temporary.replace(self.path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(pause)
