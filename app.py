"""
CaseFinder - Web UI
Serves search interface and settings for the Exocad project database.

Requirements:
    pip install flask

Usage:
    python app.py
    Open http://localhost:5000
"""

import sqlite3
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string
from datetime import datetime

app = Flask(__name__)
DB_PATH = "exocad_projects.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    return {
        "id": row["id"],
        "folder_name": row["folder_name"],
        "folder_path": row["folder_path"],
        "project_date": row["project_date"],
        "clinic": row["clinic"],
        "doctor": row["doctor"],
        "patient": row["patient"],
        "is_duplicate": bool(row["is_duplicate"]),
    }


# ─────────────────────────────────────────────
#  API — SEARCH
# ─────────────────────────────────────────────

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"clinics": [], "doctors": [], "patients": []})

    like = f"%{q}%"
    conn = get_db()

    clinics = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE clinic LIKE ? ORDER BY clinic, project_date DESC", (like,)
    )]
    doctors = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE doctor LIKE ? ORDER BY doctor, project_date DESC", (like,)
    )]
    patients = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE patient LIKE ? ORDER BY patient, project_date DESC", (like,)
    )]

    conn.close()
    return jsonify({"clinics": clinics, "doctors": doctors, "patients": patients})


@app.route("/api/stats")
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    clinics = conn.execute("SELECT COUNT(DISTINCT clinic) FROM projects WHERE clinic IS NOT NULL").fetchone()[0]
    duplicates = conn.execute("SELECT COUNT(*) FROM projects WHERE is_duplicate = 1").fetchone()[0]
    latest = conn.execute("SELECT project_date FROM projects ORDER BY project_date DESC LIMIT 1").fetchone()
    conn.close()
    return jsonify({
        "total": total,
        "clinics": clinics,
        "duplicates": duplicates,
        "latest": latest[0] if latest else None,
    })


# ─────────────────────────────────────────────
#  API — WATCH PATHS (settings)
# ─────────────────────────────────────────────

@app.route("/api/paths", methods=["GET"])
def get_paths():
    conn = get_db()
    rows = conn.execute("""
        SELECT w.id, w.path, w.label, w.enabled, w.added_at, w.last_scanned,
               COUNT(p.id) as project_count
        FROM watch_paths w
        LEFT JOIN projects p ON p.folder_path LIKE w.path || '%'
        GROUP BY w.id
        ORDER BY w.added_at DESC
    """).fetchall()
    conn.close()
    return jsonify([{
        "id": r["id"],
        "path": r["path"],
        "label": r["label"],
        "enabled": bool(r["enabled"]),
        "added_at": r["added_at"],
        "last_scanned": r["last_scanned"],
        "project_count": r["project_count"],
    } for r in rows])


@app.route("/api/paths", methods=["POST"])
def add_path():
    data = request.json
    path = (data.get("path") or "").strip()
    label = (data.get("label") or "").strip() or Path(path).name

    if not path:
        return jsonify({"error": "Path is required"}), 400

    if not Path(path).exists():
        return jsonify({"error": f"Path not accessible: {path}"}), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO watch_paths (path, label, enabled, added_at) VALUES (?, ?, 1, ?)",
            (path, label, datetime.now().isoformat(timespec="seconds"))
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Path already exists"}), 409


@app.route("/api/paths/<int:path_id>", methods=["PATCH"])
def update_path(path_id):
    data = request.json
    conn = get_db()

    if "enabled" in data:
        conn.execute(
            "UPDATE watch_paths SET enabled = ? WHERE id = ?",
            (1 if data["enabled"] else 0, path_id)
        )
    if "label" in data:
        conn.execute(
            "UPDATE watch_paths SET label = ? WHERE id = ?",
            (data["label"].strip(), path_id)
        )

    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/paths/<int:path_id>", methods=["DELETE"])
def delete_path(path_id):
    conn = get_db()
    row = conn.execute("SELECT path FROM watch_paths WHERE id = ?", (path_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404

    # Remove associated projects
    conn.execute("DELETE FROM projects WHERE folder_path LIKE ?", (row["path"] + "%",))
    conn.execute("DELETE FROM watch_paths WHERE id = ?", (path_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(SEARCH_HTML)


@app.route("/settings")
def settings():
    return render_template_string(SETTINGS_HTML)


# ─────────────────────────────────────────────
#  SHARED STYLES
# ─────────────────────────────────────────────

SHARED_CSS = """
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0e1117;
    --surface:   #161b25;
    --surface2:  #1c2333;
    --border:    #232b3a;
    --accent:    #f5a623;
    --accent2:   #e8793a;
    --text:      #e8eaf0;
    --muted:     #6b7a99;
    --clinic:    #4a9eff;
    --doctor:    #7ed4a0;
    --patient:   #c792ea;
    --danger:    #ff6b6b;
    --warning:   #ffd166;
    --radius:    10px;
  }

  body {
    font-family: 'Syne', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding-bottom: 80px;
  }

  header {
    padding: 28px 40px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
    background: var(--surface);
    position: sticky; top: 0; z-index: 100;
  }

  .logo { flex-shrink: 0; text-decoration: none; }

  .brand a {
    text-decoration: none; color: inherit;
  }

  .brand h1 { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; line-height: 1; }
  .brand p  { font-size: 11px; color: var(--muted); font-family: 'DM Mono', monospace; margin-top: 2px; }

  .header-right {
    margin-left: auto;
    display: flex; align-items: center; gap: 16px;
  }

  .stats {
    display: flex; gap: 20px;
    font-family: 'DM Mono', monospace;
    font-size: 11px; color: var(--muted); text-align: right;
  }

  .stats span { color: var(--accent); font-weight: 500; }

  .stats .dup-count span { color: var(--warning); }

  .settings-btn {
    width: 36px; height: 36px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    color: var(--muted); font-size: 18px;
    cursor: pointer; text-decoration: none;
    transition: all 0.15s;
  }

  .settings-btn:hover { border-color: var(--accent); color: var(--accent); }
  .settings-btn.active { border-color: var(--accent); color: var(--accent); background: rgba(245,166,35,0.08); }

  /* Buttons */
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 8px 16px; border-radius: 8px;
    font-family: 'Syne', sans-serif; font-size: 13px; font-weight: 600;
    cursor: pointer; transition: all 0.15s; border: none;
  }

  .btn-primary {
    background: var(--accent); color: #0e1117;
  }
  .btn-primary:hover { background: var(--accent2); }

  .btn-ghost {
    background: transparent; color: var(--muted);
    border: 1px solid var(--border);
  }
  .btn-ghost:hover { border-color: var(--accent); color: var(--accent); }

  .btn-danger {
    background: transparent; color: var(--danger);
    border: 1px solid rgba(255,107,107,0.3);
  }
  .btn-danger:hover { background: rgba(255,107,107,0.1); }

  .tag {
    font-size: 10px; font-family: 'DM Mono', monospace; font-weight: 500;
    padding: 3px 10px; border-radius: 20px;
    letter-spacing: 0.08em; text-transform: uppercase;
  }

  .tag-clinic  { background: rgba(74,158,255,0.15);  color: var(--clinic); }
  .tag-doctor  { background: rgba(126,212,160,0.15); color: var(--doctor); }
  .tag-patient { background: rgba(199,146,234,0.15); color: var(--patient); }
  .tag-warn    { background: rgba(255,209,102,0.15); color: var(--warning); }
"""

# ─────────────────────────────────────────────
#  SEARCH PAGE
# ─────────────────────────────────────────────

SEARCH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CaseFinder</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """

  .search-wrap { padding: 32px 40px 0; max-width: 900px; }

  .search-box { position: relative; }

  .search-box input {
    width: 100%; padding: 16px 20px 16px 52px;
    font-size: 17px; font-family: 'Syne', sans-serif; font-weight: 600;
    background: var(--surface); border: 2px solid var(--border);
    border-radius: var(--radius); color: var(--text); outline: none;
    transition: border-color 0.2s;
  }

  .search-box input:focus { border-color: var(--accent); }
  .search-box input::placeholder { color: var(--muted); font-weight: 400; }

  .search-icon {
    position: absolute; left: 18px; top: 50%; transform: translateY(-50%);
    color: var(--muted); font-size: 18px; pointer-events: none;
  }

  .hint {
    margin-top: 10px; font-size: 12px;
    font-family: 'DM Mono', monospace; color: var(--muted); padding-left: 4px;
  }

  .results { padding: 28px 40px 0; max-width: 1200px; display: flex; flex-direction: column; gap: 32px; }

  .empty-state {
    padding: 60px 40px; text-align: center;
    color: var(--muted); font-family: 'DM Mono', monospace; font-size: 13px;
  }

  .group-label { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  .group-count { font-size: 12px; font-family: 'DM Mono', monospace; color: var(--muted); }

  .table-wrap { border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }

  thead th {
    background: var(--surface); padding: 10px 16px; text-align: left;
    font-size: 10px; font-family: 'DM Mono', monospace;
    text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); border-bottom: 1px solid var(--border); font-weight: 500;
  }

  tbody tr { border-bottom: 1px solid var(--border); transition: background 0.15s; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: rgba(255,255,255,0.03); }
  tbody tr.is-duplicate { background: rgba(255,209,102,0.04); }

  td { padding: 11px 16px; vertical-align: middle; }

  .date-cell   { font-family: 'DM Mono', monospace; font-size: 12px; color: var(--muted); white-space: nowrap; }
  .clinic-cell { color: var(--clinic); font-weight: 600; }
  .doctor-cell { color: var(--doctor); }
  .patient-cell{ color: var(--patient); font-weight: 600; }

  .path-cell {
    font-family: 'DM Mono', monospace; font-size: 11px; color: var(--muted);
    max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  .copy-btn {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 10px; font-size: 11px; font-family: 'DM Mono', monospace;
    background: transparent; border: 1px solid var(--border);
    border-radius: 6px; color: var(--muted); cursor: pointer; transition: all 0.15s;
    white-space: nowrap;
  }

  .copy-btn:hover { border-color: var(--accent); color: var(--accent); background: rgba(245,166,35,0.08); }
  .copy-btn.copied { border-color: var(--doctor); color: var(--doctor); background: rgba(126,212,160,0.08); }

  .dup-badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 10px; font-family: 'DM Mono', monospace;
    color: var(--warning); background: rgba(255,209,102,0.12);
    border: 1px solid rgba(255,209,102,0.25);
    border-radius: 4px; padding: 2px 7px;
    white-space: nowrap;
  }

  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .loading { padding: 40px; text-align: center; font-family: 'DM Mono', monospace; font-size: 12px; color: var(--muted); animation: pulse 1.2s ease-in-out infinite; }
</style>
</head>
<body>

<header>
  <a href="/" class="logo"><img src="/static/yclab-icon-192.png" style="width:40px;height:40px;border-radius:9px;display:block;"></a>
  <div class="brand">
    <a href="/"><h1>CaseFinder</h1></a>
    <p>Exocad Project Lookup — YC Lab</p>
  </div>
  <div class="header-right">
    <div class="stats" id="stats">
      <div><span id="stat-total">—</span><br>projects</div>
      <div><span id="stat-clinics">—</span><br>clinics</div>
      <div class="dup-count"><span id="stat-dups">—</span><br>duplicates</div>
      <div><span id="stat-latest">—</span><br>latest</div>
    </div>
    <a href="/settings" class="settings-btn" title="Manage watch folders">⚙</a>
  </div>
</header>

<div class="search-wrap">
  <div class="search-box">
    <span class="search-icon">⌕</span>
    <input type="text" id="search" placeholder="Search by clinic, doctor, or patient name..." autocomplete="off" autofocus>
  </div>
  <p class="hint">Type at least 2 characters — results grouped by clinic, doctor, and patient matches</p>
</div>

<div class="results" id="results">
  <div class="empty-state">Start typing to search cases</div>
</div>

<script>
const searchEl = document.getElementById('search');
const resultsEl = document.getElementById('results');
let debounceTimer;

fetch('/api/stats').then(r => r.json()).then(d => {
  document.getElementById('stat-total').textContent   = d.total.toLocaleString();
  document.getElementById('stat-clinics').textContent = d.clinics.toLocaleString();
  document.getElementById('stat-latest').textContent  = d.latest || '—';
  const dupEl = document.getElementById('stat-dups');
  dupEl.textContent = d.duplicates.toLocaleString();
  if (d.duplicates > 0) dupEl.style.color = 'var(--warning)';
});

searchEl.addEventListener('input', () => {
  clearTimeout(debounceTimer);
  const q = searchEl.value.trim();
  if (q.length < 2) {
    resultsEl.innerHTML = '<div class="empty-state">Start typing to search cases</div>';
    return;
  }
  resultsEl.innerHTML = '<div class="loading">Searching...</div>';
  debounceTimer = setTimeout(() => doSearch(q), 250);
});

async function doSearch(q) {
  const res  = await fetch('/api/search?q=' + encodeURIComponent(q));
  const data = await res.json();
  const total = data.clinics.length + data.doctors.length + data.patients.length;
  if (total === 0) {
    resultsEl.innerHTML = '<div class="empty-state">No results found for &ldquo;' + escHtml(q) + '&rdquo;</div>';
    return;
  }
  let html = '';
  if (data.clinics.length)  html += renderGroup('clinic',  'Clinic',  data.clinics);
  if (data.doctors.length)  html += renderGroup('doctor',  'Doctor',  data.doctors);
  if (data.patients.length) html += renderGroup('patient', 'Patient', data.patients);
  resultsEl.innerHTML = html;
}

function renderGroup(type, label, rows) {
  return `
    <div class="group">
      <div class="group-label">
        <span class="tag tag-${type}">${label}</span>
        <span class="group-count">${rows.length} result${rows.length !== 1 ? 's' : ''}</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Date</th><th>Clinic</th><th>Doctor</th><th>Patient</th><th>Folder Path</th><th></th><th></th>
          </tr></thead>
          <tbody>${rows.map(renderRow).join('')}</tbody>
        </table>
      </div>
    </div>`;
}

function renderRow(r) {
  const path  = r.folder_path || '';
  const btnId = 'btn-' + r.id;
  const dupBadge = r.is_duplicate
    ? '<span class="dup-badge">⚠ duplicate</span>'
    : '';
  return `
    <tr class="${r.is_duplicate ? 'is-duplicate' : ''}">
      <td class="date-cell">${r.project_date || '—'}</td>
      <td class="clinic-cell">${escHtml(r.clinic  || '—')}</td>
      <td class="doctor-cell">${escHtml(r.doctor  || '—')}</td>
      <td class="patient-cell">${escHtml(r.patient || '—')}</td>
      <td class="path-cell" title="${escHtml(path)}">${escHtml(path)}</td>
      <td>${dupBadge}</td>
      <td>
        <button class="copy-btn" id="${btnId}" data-path="${escHtml(path)}" onclick="copyPath(this)">
          ⎘ Copy path
        </button>
      </td>
    </tr>`;
}

function copyPath(btn) {
  const path = btn.getAttribute('data-path');
  navigator.clipboard.writeText(path).then(() => {
    btn.textContent = '✓ Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.innerHTML = '⎘ Copy path'; btn.classList.remove('copied'); }, 2000);
  });
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
#  SETTINGS PAGE
# ─────────────────────────────────────────────

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CaseFinder — Settings</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """

  .page { padding: 36px 40px; max-width: 860px; }

  .page-title {
    font-size: 24px; font-weight: 800; letter-spacing: -0.5px; margin-bottom: 6px;
  }

  .page-subtitle { font-size: 13px; color: var(--muted); font-family: 'DM Mono', monospace; margin-bottom: 32px; }

  /* Add form */
  .add-form {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px 24px; margin-bottom: 32px;
  }

  .add-form h2 { font-size: 14px; font-weight: 700; margin-bottom: 16px; color: var(--text); }

  .form-row { display: flex; gap: 12px; align-items: flex-start; }

  .form-group { display: flex; flex-direction: column; gap: 6px; flex: 1; }

  .form-group label { font-size: 11px; font-family: 'DM Mono', monospace; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }

  .form-group input {
    padding: 10px 14px; background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; color: var(--text); font-family: 'DM Mono', monospace;
    font-size: 13px; outline: none; transition: border-color 0.2s;
  }

  .form-group input:focus { border-color: var(--accent); }
  .form-group input::placeholder { color: var(--muted); }

  .form-error { font-size: 12px; font-family: 'DM Mono', monospace; color: var(--danger); margin-top: 10px; display: none; }
  .form-success { font-size: 12px; font-family: 'DM Mono', monospace; color: var(--doctor); margin-top: 10px; display: none; }

  /* Path list */
  .section-title { font-size: 13px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 14px; font-family: 'DM Mono', monospace; }

  .path-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 16px 20px;
    margin-bottom: 10px; display: flex; align-items: center; gap: 16px;
    transition: border-color 0.15s;
  }

  .path-card:hover { border-color: rgba(255,255,255,0.1); }
  .path-card.disabled { opacity: 0.5; }

  .path-info { flex: 1; min-width: 0; }

  .path-label {
    font-size: 14px; font-weight: 700; margin-bottom: 4px;
    display: flex; align-items: center; gap: 8px;
  }

  .path-str {
    font-family: 'DM Mono', monospace; font-size: 11px; color: var(--muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  .path-meta {
    font-family: 'DM Mono', monospace; font-size: 11px; color: var(--muted);
    margin-top: 6px; display: flex; gap: 16px;
  }

  .path-meta span { color: var(--text); }

  .path-actions { display: flex; gap: 8px; flex-shrink: 0; }

  .toggle-btn {
    width: 40px; height: 22px; border-radius: 11px; border: none; cursor: pointer;
    position: relative; transition: background 0.2s; flex-shrink: 0;
  }

  .toggle-btn::after {
    content: ''; position: absolute; width: 16px; height: 16px;
    background: white; border-radius: 50%; top: 3px; transition: left 0.2s;
  }

  .toggle-btn.on  { background: var(--accent); }
  .toggle-btn.on::after  { left: 21px; }
  .toggle-btn.off { background: var(--border); }
  .toggle-btn.off::after { left: 3px; }

  .empty-paths { padding: 40px; text-align: center; color: var(--muted); font-family: 'DM Mono', monospace; font-size: 13px; }

  .badge-count { background: var(--surface2); border: 1px solid var(--border); border-radius: 20px; padding: 1px 8px; font-size: 11px; font-family: 'DM Mono', monospace; color: var(--muted); }
</style>
</head>
<body>

<header>
  <a href="/" class="logo">CF</a>
  <div class="brand">
    <a href="/"><h1>CaseFinder</h1></a>
    <p>Exocad Project Lookup — YC Lab</p>
  </div>
  <div class="header-right">
    <a href="/settings" class="settings-btn active" title="Settings">⚙</a>
  </div>
</header>

<div class="page">
  <div class="page-title">Watch Folders</div>
  <div class="page-subtitle">Folders added here are scanned automatically. The scanner picks up new entries within 30 seconds.</div>

  <!-- Add form -->
  <div class="add-form">
    <h2>Add a folder to watch</h2>
    <div class="form-row">
      <div class="form-group" style="flex:2">
        <label>Folder Path</label>
        <input type="text" id="input-path" placeholder="/Volumes/YCLab_ALL/Data/CAD-Data/...">
      </div>
      <div class="form-group" style="flex:1">
        <label>Label (optional)</label>
        <input type="text" id="input-label" placeholder="e.g. 2025-01">
      </div>
      <div class="form-group" style="flex:0; justify-content: flex-end; padding-top: 22px;">
        <button class="btn btn-primary" onclick="addPath()">+ Add</button>
      </div>
    </div>
    <div class="form-error" id="form-error"></div>
    <div class="form-success" id="form-success"></div>
  </div>

  <!-- Path list -->
  <div class="section-title">Watched Folders</div>
  <div id="path-list"><div class="empty-paths">Loading...</div></div>
</div>

<script>
async function loadPaths() {
  const res  = await fetch('/api/paths');
  const rows = await res.json();
  const el   = document.getElementById('path-list');

  if (rows.length === 0) {
    el.innerHTML = '<div class="empty-paths">No folders added yet.</div>';
    return;
  }

  el.innerHTML = rows.map(r => `
    <div class="path-card ${r.enabled ? '' : 'disabled'}" id="card-${r.id}">
      <div class="path-info">
        <div class="path-label">
          ${escHtml(r.label || r.path)}
          <span class="badge-count">${r.project_count} projects</span>
        </div>
        <div class="path-str" title="${escHtml(r.path)}">${escHtml(r.path)}</div>
        <div class="path-meta">
          Added: <span>${r.added_at ? r.added_at.slice(0,10) : '—'}</span>
          &nbsp;|&nbsp;
          Last scanned: <span>${r.last_scanned ? r.last_scanned.slice(0,19).replace('T',' ') : 'not yet'}</span>
        </div>
      </div>
      <div class="path-actions">
        <button class="toggle-btn ${r.enabled ? 'on' : 'off'}" title="${r.enabled ? 'Disable' : 'Enable'}" onclick="togglePath(${r.id}, ${r.enabled})"></button>
        <button class="btn btn-danger" onclick="deletePath(${r.id}, '${escHtml(r.label || r.path)}')">Remove</button>
      </div>
    </div>
  `).join('');
}

async function addPath() {
  const path  = document.getElementById('input-path').value.trim();
  const label = document.getElementById('input-label').value.trim();
  const errEl = document.getElementById('form-error');
  const okEl  = document.getElementById('form-success');
  errEl.style.display = 'none';
  okEl.style.display  = 'none';

  if (!path) { showError('Please enter a folder path.'); return; }

  const res  = await fetch('/api/paths', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path, label}),
  });
  const data = await res.json();

  if (!res.ok) {
    showError(data.error || 'Failed to add path.');
  } else {
    document.getElementById('input-path').value  = '';
    document.getElementById('input-label').value = '';
    okEl.textContent = 'Folder added! The scanner will pick it up within 30 seconds.';
    okEl.style.display = 'block';
    setTimeout(() => okEl.style.display = 'none', 4000);
    loadPaths();
  }
}

async function togglePath(id, currentlyEnabled) {
  await fetch('/api/paths/' + id, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled: !currentlyEnabled}),
  });
  loadPaths();
}

async function deletePath(id, label) {
  if (!confirm('Remove "' + label + '" and all its indexed projects from the database?')) return;
  await fetch('/api/paths/' + id, {method: 'DELETE'});
  loadPaths();
}

function showError(msg) {
  const el = document.getElementById('form-error');
  el.textContent = msg;
  el.style.display = 'block';
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

loadPaths();

// Auto-refresh path list every 30s to reflect scanner updates
setInterval(loadPaths, 30000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    if not Path(DB_PATH).exists():
        print(f"ERROR: Database not found at '{DB_PATH}'")
        print("Make sure scanner.py has been run first.")
    else:
        print("CaseFinder running at http://localhost:5000")
        app.run(debug=True, port=5000)