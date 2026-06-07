"""
CaseFinder - Exocad Project Folder Scanner
- Scans designated folders for Exocad project directories
- Uses Gemini to intelligently parse clinic, doctor, and patient names
- Stores results in SQLite database
- Watches for new/changed folders and updates DB automatically

Requirements:
    pip install google-genai watchdog python-dotenv

Usage:
    Add GEMINI_API_KEY to your .env file
    Run: python scanner.py
"""

import os
import re
import json
import sqlite3
import logging
import time
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

load_dotenv()


# ─────────────────────────────────────────────
#  CONFIGURATION  — edit these before running
# ─────────────────────────────────────────────

# Paths to scan. Use raw strings (r"...") for Windows UNC paths.
WATCH_PATHS = [
    r"/Volumes/YCLab_ALL/Data/CAD-Data/DO NOT delet MSI CAD-Data/2024/2024-05",
    # Add more paths here as needed:
    # r"\\DXP6800PRO-86DD\YCLab_ALL\Data\CAD-Data\DO NOT delet CAD-Data",
]

DB_PATH = "exocad_projects.db"

GEMINI_MODEL = "gemini-2.5-flash"

# Minimum seconds between re-scans triggered by watchdog events
RESCAN_DEBOUNCE_SECONDS = 5


# ─────────────────────────────────────────────
#  LOGGING SETUP
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("casefinder.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  GEMINI CLIENT
# ─────────────────────────────────────────────

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


# ─────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_name   TEXT    NOT NULL UNIQUE,
            folder_path   TEXT    NOT NULL,
            project_date  TEXT,
            clinic        TEXT,
            doctor        TEXT,
            patient       TEXT,
            raw_parse     TEXT,
            parse_method  TEXT,
            first_seen    TEXT    NOT NULL,
            last_seen     TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_clinic  ON projects(clinic);
        CREATE INDEX IF NOT EXISTS idx_doctor  ON projects(doctor);
        CREATE INDEX IF NOT EXISTS idx_patient ON projects(patient);
        CREATE INDEX IF NOT EXISTS idx_date    ON projects(project_date);
    """)
    conn.commit()
    log.info("Database ready: %s", DB_PATH)


# ─────────────────────────────────────────────
#  GEMINI PARSING
# ─────────────────────────────────────────────

GEMINI_PROMPT = """You are a dental lab data parser. Extract structured information from the dental lab project folder name below.

Rules:
- date: extract as YYYY-MM-DD. If missing, return null.
- clinic: the dental clinic name only, no extra words.
- doctor: full name WITHOUT the "Dr." prefix and without any title. If only initials are given (e.g. "Dr.SA"), return the initials as-is.
- patient: full name. If written in CamelCase (e.g. "JohnSmith"), split into "John Smith". Remove any digits embedded in the name (e.g. "Roger2Skirrow" -> "Roger Skirrow", "Lorna Irons1st" -> "Lorna Irons"). Strip trailing suffixes like "- Copy".
- If a field cannot be determined, return null.

Respond ONLY with a valid JSON object - no explanation, no markdown, no code fences.

Example input:  2025-01-03_Sherwood dental - Dr. Barkwell, Steven-CallumSundquist
Example output: {"date":"2025-01-03","clinic":"Sherwood Dental","doctor":"Barkwell, Steven","patient":"Callum Sundquist"}"""


def parse_with_gemini(folder_name: str) -> dict:
    """Call Gemini to extract structured fields from a folder name."""
    clean_name = re.sub(r"\s*-\s*copy\s*$", "", folder_name, flags=re.IGNORECASE).strip()

    # Append folder name separately to avoid .format() clashing with JSON braces
    prompt = GEMINI_PROMPT + "\n\nFolder name: " + clean_name

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()
        # Strip markdown fences if Gemini adds them anyway
        text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text)
        parsed = json.loads(text)
        log.debug("Gemini parsed '%s' -> %s", folder_name, parsed)
        return parsed
    except json.JSONDecodeError as e:
        log.warning("Gemini JSON parse error for '%s': %s | raw: %s", folder_name, e, text)
    except Exception as e:
        log.warning("Gemini API error for '%s': %s", folder_name, e)

    return {"date": None, "clinic": None, "doctor": None, "patient": None}


def parse_date_from_name(folder_name: str) -> str | None:
    """Local fallback: extract date from folder name prefix."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", folder_name)
    return m.group(1) if m else None


# ─────────────────────────────────────────────
#  SCANNER
# ─────────────────────────────────────────────

PROJECT_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_")


def looks_like_project_folder(name: str) -> bool:
    return bool(PROJECT_FOLDER_RE.match(name))


def upsert_project(conn: sqlite3.Connection, folder_name: str, folder_path: str):
    """Parse and insert/update a single project folder in the database."""
    now = datetime.now().isoformat(timespec="seconds")

    existing = conn.execute(
        "SELECT id FROM projects WHERE folder_name = ?", (folder_name,)
    ).fetchone()

    if existing:
        # Already indexed — just refresh timestamps and path
        conn.execute(
            "UPDATE projects SET last_seen = ?, folder_path = ? WHERE folder_name = ?",
            (now, folder_path, folder_name),
        )
        conn.commit()
        return

    # New folder — parse with Gemini
    parsed = parse_with_gemini(folder_name)

    # Local fallback for date if Gemini missed it
    project_date = parsed.get("date") or parse_date_from_name(folder_name)

    conn.execute(
        """
        INSERT INTO projects
            (folder_name, folder_path, project_date, clinic, doctor, patient,
             raw_parse, parse_method, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(folder_name) DO UPDATE SET
            folder_path   = excluded.folder_path,
            project_date  = excluded.project_date,
            clinic        = excluded.clinic,
            doctor        = excluded.doctor,
            patient       = excluded.patient,
            raw_parse     = excluded.raw_parse,
            parse_method  = excluded.parse_method,
            last_seen     = excluded.last_seen
        """,
        (
            folder_name,
            folder_path,
            project_date,
            parsed.get("clinic"),
            parsed.get("doctor"),
            parsed.get("patient"),
            json.dumps(parsed),
            "gemini",
            now,
            now,
        ),
    )
    conn.commit()
    log.info(
        "Indexed: %s | %s | Dr. %s | %s",
        project_date,
        parsed.get("clinic", "?"),
        parsed.get("doctor", "?"),
        parsed.get("patient", "?"),
    )


def scan_path(conn: sqlite3.Connection, base_path: str):
    """Recursively scan base_path for Exocad project folders at any depth."""
    log.info("Scanning: %s", base_path)
    base = Path(base_path)

    if not base.exists():
        log.warning("Path not accessible: %s", base_path)
        return

    count = 0
    for entry in base.rglob("*"):
        if entry.is_dir() and looks_like_project_folder(entry.name):
            upsert_project(conn, entry.name, str(entry))
            count += 1

    log.info("Scan complete: %d project folders found in %s", count, base_path)


def full_scan(conn: sqlite3.Connection):
    """Scan all configured watch paths."""
    for path in WATCH_PATHS:
        scan_path(conn, path)


# ─────────────────────────────────────────────
#  WATCHDOG — live folder monitoring
# ─────────────────────────────────────────────

class ProjectFolderHandler(FileSystemEventHandler):
    """Watches for new/moved directories and triggers rescans."""

    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule_rescan(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(RESCAN_DEBOUNCE_SECONDS, self._do_rescan)
            self._timer.start()

    def _do_rescan(self):
        log.info("Watchdog triggered rescan...")
        full_scan(self.conn)

    def on_created(self, event):
        if event.is_directory:
            name = Path(event.src_path).name
            if looks_like_project_folder(name):
                log.info("New project folder detected: %s", name)
                upsert_project(self.conn, name, event.src_path)
            else:
                # Could be a new month folder — do a broader rescan
                self._schedule_rescan()

    def on_moved(self, event):
        if event.is_directory:
            old_name = Path(event.src_path).name
            new_name = Path(event.dest_path).name
            # Remove old entry
            self.conn.execute("DELETE FROM projects WHERE folder_name = ?", (old_name,))
            self.conn.commit()
            log.info("Removed renamed folder: %s", old_name)
            # Index the new name if it looks like a project
            if looks_like_project_folder(new_name):
                upsert_project(self.conn, new_name, event.dest_path)
            
    def on_deleted(self, event):
        if event.is_directory:
            name = Path(event.src_path).name
            if looks_like_project_folder(name):
                conn = self.conn
                conn.execute("DELETE FROM projects WHERE folder_name = ?", (name,))
                conn.commit()
                log.info("Removed from DB: %s", name)


def start_watcher(conn: sqlite3.Connection) -> Observer:
    observer = Observer()
    handler = ProjectFolderHandler(conn)
    for path in WATCH_PATHS:
        if Path(path).exists():
            observer.schedule(handler, path, recursive=True)
            log.info("Watching: %s", path)
        else:
            log.warning("Cannot watch (path unavailable): %s", path)
    observer.start()
    return observer


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    log.info("=== CaseFinder Scanner starting ===")

    conn = get_connection()
    init_db(conn)
    full_scan(conn)

    observer = start_watcher(conn)

    log.info("Scanner running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Stopping...")
    finally:
        observer.stop()
        observer.join()
        conn.close()
        log.info("Scanner stopped.")


if __name__ == "__main__":
    main()