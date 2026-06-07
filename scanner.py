"""
CaseFinder - Exocad Project Folder Scanner
- Reads watch paths from SQLite DB (managed via web UI)
- Scans designated folders for Exocad project directories
- Uses Gemini to intelligently parse clinic, doctor, and patient names
- Watches for new/changed/deleted folders and updates DB automatically
- Flags duplicate folder names across different paths

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
#  CONFIGURATION
# ─────────────────────────────────────────────

# Bootstrap path — used only on very first run before any paths are added via UI.
# After first run, paths are managed in the database via the web UI.
BOOTSTRAP_PATHS = [
    r"/Users/wayne/dev/projects/yclab/casefinder/exocad",
    # r"\\DXP6800PRO-86DD\YCLab_ALL\Data\CAD-Data\DO NOT delet CAD-Data",
]

DB_PATH = "exocad_projects.db"
GEMINI_MODEL = "gemini-2.5-flash"
RESCAN_DEBOUNCE_SECONDS = 5

# How often (seconds) the scanner polls the DB for newly added watch paths
POLL_INTERVAL_SECONDS = 30


# ─────────────────────────────────────────────
#  LOGGING
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
        CREATE TABLE IF NOT EXISTS watch_paths (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            path         TEXT    NOT NULL UNIQUE,
            label        TEXT,
            enabled      INTEGER NOT NULL DEFAULT 1,
            added_at     TEXT    NOT NULL,
            last_scanned TEXT
        );

        CREATE TABLE IF NOT EXISTS projects (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_name   TEXT    NOT NULL,
            folder_path   TEXT    NOT NULL UNIQUE,
            project_date  TEXT,
            clinic        TEXT,
            doctor        TEXT,
            patient       TEXT,
            raw_parse     TEXT,
            parse_method  TEXT,
            is_duplicate  INTEGER NOT NULL DEFAULT 0,
            first_seen    TEXT    NOT NULL,
            last_seen     TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_clinic      ON projects(clinic);
        CREATE INDEX IF NOT EXISTS idx_doctor      ON projects(doctor);
        CREATE INDEX IF NOT EXISTS idx_patient     ON projects(patient);
        CREATE INDEX IF NOT EXISTS idx_date        ON projects(project_date);
        CREATE INDEX IF NOT EXISTS idx_folder_name ON projects(folder_name);
        CREATE INDEX IF NOT EXISTS idx_duplicate   ON projects(is_duplicate);
    """)
    conn.commit()
    log.info("Database ready: %s", DB_PATH)


def bootstrap_watch_paths(conn: sqlite3.Connection):
    """Insert bootstrap paths into watch_paths table if no paths exist yet."""
    count = conn.execute("SELECT COUNT(*) FROM watch_paths").fetchone()[0]
    if count == 0:
        now = datetime.now().isoformat(timespec="seconds")
        for path in BOOTSTRAP_PATHS:
            try:
                conn.execute(
                    "INSERT INTO watch_paths (path, label, enabled, added_at) VALUES (?, ?, 1, ?)",
                    (path, Path(path).name, now)
                )
                log.info("Bootstrap watch path added: %s", path)
            except sqlite3.IntegrityError:
                pass
        conn.commit()


def get_enabled_paths(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, path, label FROM watch_paths WHERE enabled = 1"
    ).fetchall()
    return [{"id": r["id"], "path": r["path"], "label": r["label"]} for r in rows]


def update_last_scanned(conn: sqlite3.Connection, path_id: int):
    conn.execute(
        "UPDATE watch_paths SET last_scanned = ? WHERE id = ?",
        (datetime.now().isoformat(timespec="seconds"), path_id)
    )
    conn.commit()


# ─────────────────────────────────────────────
#  DUPLICATE DETECTION
# ─────────────────────────────────────────────

def check_and_flag_duplicates(conn: sqlite3.Connection, folder_name: str):
    """If multiple rows share the same folder_name, flag all as duplicates."""
    rows = conn.execute(
        "SELECT id FROM projects WHERE folder_name = ?", (folder_name,)
    ).fetchall()
    if len(rows) > 1:
        ids = [r["id"] for r in rows]
        conn.execute(
            f"UPDATE projects SET is_duplicate = 1 WHERE id IN ({','.join('?' * len(ids))})",
            ids
        )
        conn.commit()
        log.info("Duplicate flagged: %s (%d copies)", folder_name, len(rows))
    else:
        # Clear duplicate flag if only one remains
        conn.execute(
            "UPDATE projects SET is_duplicate = 0 WHERE folder_name = ?", (folder_name,)
        )
        conn.commit()


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
    clean_name = re.sub(r"\s*-\s*copy\s*$", "", folder_name, flags=re.IGNORECASE).strip()
    prompt = GEMINI_PROMPT + "\n\nFolder name: " + clean_name
    try:
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        text = response.text.strip()
        text = re.sub(r"^```[a-z]*\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text)
        parsed = json.loads(text)
        log.debug("Gemini parsed '%s' -> %s", folder_name, parsed)
        return parsed
    except json.JSONDecodeError as e:
        log.warning("Gemini JSON parse error for '%s': %s", folder_name, e)
    except Exception as e:
        log.warning("Gemini API error for '%s': %s", folder_name, e)
    return {"date": None, "clinic": None, "doctor": None, "patient": None}


def parse_date_from_name(folder_name: str) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", folder_name)
    return m.group(1) if m else None


# ─────────────────────────────────────────────
#  SCANNER
# ─────────────────────────────────────────────

PROJECT_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_")


def looks_like_project_folder(name: str) -> bool:
    return bool(PROJECT_FOLDER_RE.match(name))


def upsert_project(conn: sqlite3.Connection, folder_name: str, folder_path: str):
    """Insert or update a project. Unique on folder_path; flags duplicates by folder_name."""
    now = datetime.now().isoformat(timespec="seconds")

    existing = conn.execute(
        "SELECT id FROM projects WHERE folder_path = ?", (folder_path,)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE projects SET last_seen = ? WHERE folder_path = ?",
            (now, folder_path),
        )
        conn.commit()
        return

    # New entry — parse with Gemini
    parsed = parse_with_gemini(folder_name)
    project_date = parsed.get("date") or parse_date_from_name(folder_name)

    conn.execute(
        """
        INSERT INTO projects
            (folder_name, folder_path, project_date, clinic, doctor, patient,
             raw_parse, parse_method, is_duplicate, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(folder_path) DO UPDATE SET
            last_seen = excluded.last_seen
        """,
        (
            folder_name, folder_path, project_date,
            parsed.get("clinic"), parsed.get("doctor"), parsed.get("patient"),
            json.dumps(parsed), "gemini", now, now,
        ),
    )
    conn.commit()

    # Check for duplicates after insert
    check_and_flag_duplicates(conn, folder_name)

    log.info(
        "Indexed: %s | %s | Dr. %s | %s",
        project_date,
        parsed.get("clinic", "?"),
        parsed.get("doctor", "?"),
        parsed.get("patient", "?"),
    )


def delete_project_by_path(conn: sqlite3.Connection, folder_path: str):
    folder_name_result = conn.execute(
        "SELECT folder_name FROM projects WHERE folder_path = ? OR folder_path LIKE ?",
        (folder_path, folder_path + "%")
    ).fetchone()

    result = conn.execute(
        "DELETE FROM projects WHERE folder_path = ? OR folder_path LIKE ?",
        (folder_path, folder_path + "%")
    )
    conn.commit()

    if result.rowcount > 0:
        log.info("Removed from DB: %s", folder_path)
        # Re-check duplicate status for remaining entries with same folder_name
        if folder_name_result:
            check_and_flag_duplicates(conn, folder_name_result["folder_name"])


def scan_path(conn: sqlite3.Connection, path_info: dict):
    """Recursively scan a watch path for Exocad project folders."""
    base_path = path_info["path"]
    path_id = path_info["id"]

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

    update_last_scanned(conn, path_id)
    log.info("Scan complete: %d project folders found in %s", count, base_path)


def full_scan(conn: sqlite3.Connection):
    for path_info in get_enabled_paths(conn):
        scan_path(conn, path_info)


# ─────────────────────────────────────────────
#  WATCHDOG
# ─────────────────────────────────────────────

class ProjectFolderHandler(FileSystemEventHandler):

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
                self._schedule_rescan()

    def on_deleted(self, event):
        log.info("Delete event — is_dir: %s | path: %s", event.is_directory, event.src_path)
        delete_project_by_path(self.conn, event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            old_name = Path(event.src_path).name
            new_name = Path(event.dest_path).name
            log.info("Folder renamed: %s -> %s", old_name, new_name)
            delete_project_by_path(self.conn, event.src_path)
            if looks_like_project_folder(new_name):
                upsert_project(self.conn, new_name, event.dest_path)


# ─────────────────────────────────────────────
#  PATH WATCHER MANAGER
#  Polls DB for newly added/removed watch paths
#  and updates watchdog observers accordingly
# ─────────────────────────────────────────────

class PathWatcherManager:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.observer = Observer()
        self.handler = ProjectFolderHandler(conn)
        self.watched: dict[str, object] = {}  # path -> watchdog watch handle
        self._running = False

    def start(self):
        self.observer.start()
        self._sync_watches()
        self._running = True
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()

    def stop(self):
        self._running = False
        self.observer.stop()
        self.observer.join()

    def _sync_watches(self):
        """Add/remove watchdog watches to match enabled DB paths."""
        enabled = {p["path"] for p in get_enabled_paths(self.conn)}

        # Add new paths
        for path in enabled:
            if path not in self.watched:
                if Path(path).exists():
                    watch = self.observer.schedule(self.handler, path, recursive=True)
                    self.watched[path] = watch
                    log.info("Now watching: %s", path)
                else:
                    log.warning("Cannot watch (path unavailable): %s", path)

        # Remove paths no longer enabled
        for path in list(self.watched.keys()):
            if path not in enabled:
                self.observer.unschedule(self.watched.pop(path))
                log.info("Stopped watching: %s", path)

    def _poll_loop(self):
        """Periodically check DB for new/removed paths and rescan."""
        while self._running:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                self._sync_watches()
                # Scan any paths not yet scanned
                for path_info in get_enabled_paths(self.conn):
                    row = self.conn.execute(
                        "SELECT last_scanned FROM watch_paths WHERE id = ?",
                        (path_info["id"],)
                    ).fetchone()
                    if not row["last_scanned"]:
                        log.info("New path detected, scanning: %s", path_info["path"])
                        scan_path(self.conn, path_info)
                        self._sync_watches()
            except Exception as e:
                log.error("Poll loop error: %s", e)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    log.info("=== CaseFinder Scanner starting ===")

    conn = get_connection()
    init_db(conn)
    bootstrap_watch_paths(conn)
    full_scan(conn)

    manager = PathWatcherManager(conn)
    manager.start()

    log.info("Scanner running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Stopping...")
    finally:
        manager.stop()
        conn.close()
        log.info("Scanner stopped.")


if __name__ == "__main__":
    main()