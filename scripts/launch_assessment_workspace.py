"""Launch existing Fakturama in a separate profile/data directory; no DB APIs."""

from pathlib import Path
import subprocess
import json
import argparse
import os
import re

project = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--name", help="New isolated test name; existing profiles are never overwritten"
)
parser.add_argument(
    "--executable",
    default=os.environ.get("FAKTURAMA_EXE", r"C:\Program Files\Fakturama2\Fakturama.exe"),
    help="Installed Fakturama executable (default: FAKTURAMA_EXE env var, then the standard install path)",
)
parser.add_argument(
    "--euros", action="store_true", help="Initialize EUR/Germany before any document is opened"
)
args = parser.parse_args()
if args.name and not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", args.name):
    parser.error("Name must be lowercase letters, digits and underscores, beginning with a letter")
root = project / "evidence" / "assessment_isolated"
if args.name:
    root = root / args.name
executable = Path(args.executable)
if not executable.is_file():
    parser.error("Fakturama executable does not exist")
alias = "assessment" + ("_" + args.name if args.name else "")
eclipse = root / "eclipse"
business = root / "business"
settings = eclipse / ".metadata" / ".plugins" / "org.eclipse.core.runtime" / ".settings"
prefs = settings / "com.sebulli.fakturama.rcp.prefs"
if prefs.exists():
    raise SystemExit(
        "Isolated profile already exists; inspect it and reuse its app instead of launching a duplicate."
    )
settings.mkdir(parents=True, exist_ok=True)
business.mkdir(parents=True, exist_ok=True)
path = business.as_posix().replace(":", r"\:")
db = (business / "Database" / "Database").as_posix().replace(":", r"\:")
prefs.write_text(
    rf"""eclipse.preferences.version=1
GENERAL_WORKSPACE={path}
dataSourceName={db}
isreinit=false
jakarta.persistence.jdbc.driver=org.hsqldb.jdbc.JDBCDriver
jakarta.persistence.jdbc.url=jdbc\:hsqldb\:file\:{db};shutdown=true
jakarta.persistence.jdbc.user=sa
jakarta.persistence.jdbc.password=
jdbc_reconnect=true
portNumber=9002
PRODUCT_USE_ITEMNR=true
PRODUCT_USE_DESCRIPTION=true
PRODUCT_USE_PICTURE=true
DOCUMENT_USE_DISCOUNT_EACH_ITEM=true
DOCUMENT_IMMEDIATELY_OVERTAKE_ITEMNUMBER_FROM_PRODUCTS_DIALOG=false
""" + ("PREFERENCE_CURRENCY_LOCALE=de/DE\n" if args.euros else ""),
    encoding="utf-8",
)
command = [
    str(executable),
    "-data",
    str(eclipse),
    "-vmargs",
    "-DREFRESH_BUNDLES=false",
    "-Dorg.eclipse.e4.ui.css.dynamic=true",
    "--add-modules=ALL-SYSTEM",
    "-DLiquibaseHubMode=off",
    "-Dhsql.database.name=" + alias,
]
process = subprocess.Popen(
    command, cwd=str(executable.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
(root / "launch.json").write_text(
    json.dumps(
        dict(
            pid=process.pid,
            workspace=str(business),
            eclipse_profile=str(eclipse),
            database_alias=alias,
            currency="EUR" if args.euros else "default",
            command=command,
        ),
        indent=2,
    ),
    encoding="utf-8",
)
print("Launched isolated profile PID", process.pid)
print("Business workspace:", business)
