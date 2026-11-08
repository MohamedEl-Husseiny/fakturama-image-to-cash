"""Launch an isolated EUR workspace and execute the original image workflow."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--name", required=True, help="Unique new workspace name; existing profiles are refused"
    )
    p.add_argument("--image", default=str(ROOT / "examples/source_order.png"))
    p.add_argument(
        "--executable",
        default=None,
        help="Fakturama executable; passed through to launch_assessment_workspace.py",
    )
    a = p.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", a.name):
        p.error("Invalid isolated workspace name")
    source = Path(a.image).resolve()
    if not source.is_file():
        p.error("Source image does not exist")
    workspace = ROOT / "evidence/assessment_isolated" / a.name / "business"
    profile = workspace.parent / "eclipse"
    if profile.exists() or workspace.exists():
        p.error("Workspace already exists; preserve it and choose a new name")
    evidence = ROOT / "evidence" / f"{a.name}_execution"
    if evidence.exists():
        p.error("Execution evidence already exists")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/launch_assessment_workspace.py"),
            "--name",
            a.name,
            "--euros",
        ]
        + (["--executable", a.executable] if a.executable else []),
        cwd=ROOT,
        check=True,
    )
    evidence.mkdir(parents=True)
    manifest = dict(
        started=datetime.now().isoformat(),
        workspace=str(workspace),
        currency="EUR",
        image=str(source),
        image_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        payment_before_debtor=True,
        single_document_save_required=True,
    )
    manifest_path = evidence / "fresh_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    expected = str(workspace.resolve()).replace("\\", "/").casefold()
    deadline = time.monotonic() + 120
    while True:
        candidates = []
        for window in desktop.windows(visible_only=True):
            title = window.window_text()
            if (
                title.startswith("Fakturama - ")
                and title.split(" - ", 1)[1].replace("\\", "/").casefold() == expected
            ):
                candidates.append(window)
        if len(candidates) > 1:
            raise RuntimeError("More than one window matches the new workspace")
        if len(candidates) == 1 and candidates[0].is_enabled():
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Fresh Fakturama workspace did not become ready")
        time.sleep(1)
    command = [
        sys.executable,
        str(ROOT / "fakturama_full_workflow.py"),
        "--image",
        str(source),
        "--execute",
        "--allow-new",
        "--strict-assessment",
        "--assume-fresh-workspace",  # this script created the workspace seconds ago
        "--workspace",
        str(workspace),
        "--evidence",
        str(evidence),
    ]
    result = subprocess.run(command, cwd=ROOT)
    manifest.update(
        finished=datetime.now().isoformat(),
        exit_code=result.returncode,
        uninterrupted_workflow_pass=result.returncode == 0,
        command=command,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
