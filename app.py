"""
CaseFinder - Web UI
Serves a search interface for the Exocad project database.
Run alongside scanner.py or separately.

Requirements:
    pip install flask

Usage:
    python app.py
    Open http://localhost:5000 in your browser
"""

import sqlite3
import json
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string

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
    }


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify({"clinics": [], "doctors": [], "patients": []})

    like = f"%{q}%"
    conn = get_db()

    # Results grouped by which field matched
    clinics = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE clinic LIKE ? ORDER BY clinic, project_date DESC",
        (like,)
    )]
    doctors = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE doctor LIKE ? ORDER BY doctor, project_date DESC",
        (like,)
    )]
    patients = [row_to_dict(r) for r in conn.execute(
        "SELECT * FROM projects WHERE patient LIKE ? ORDER BY patient, project_date DESC",
        (like,)
    )]

    conn.close()
    return jsonify({"clinics": clinics, "doctors": doctors, "patients": patients})


@app.route("/api/stats")
def stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    clinics = conn.execute("SELECT COUNT(DISTINCT clinic) FROM projects WHERE clinic IS NOT NULL").fetchone()[0]
    latest = conn.execute("SELECT project_date FROM projects ORDER BY project_date DESC LIMIT 1").fetchone()
    conn.close()
    return jsonify({
        "total": total,
        "clinics": clinics,
        "latest": latest[0] if latest else None
    })


# ─────────────────────────────────────────────
#  HTML / CSS / JS  (single-file UI)
# ─────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CaseFinder</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0e1117;
    --surface:   #161b25;
    --border:    #232b3a;
    --accent:    #f5a623;
    --accent2:   #e8793a;
    --text:      #e8eaf0;
    --muted:     #6b7a99;
    --clinic:    #4a9eff;
    --doctor:    #7ed4a0;
    --patient:   #c792ea;
    --radius:    10px;
  }

  body {
    font-family: 'Syne', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 0 0 80px;
  }

  /* ── Header ── */
  header {
    padding: 32px 40px 24px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 20px;
    background: var(--surface);
  }

  .logo {
    width: 44px; height: 44px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; font-weight: 800; color: #0e1117;
    flex-shrink: 0;
    letter-spacing: -1px;
  }

  .brand h1 {
    font-size: 22px; font-weight: 800;
    letter-spacing: -0.5px;
    line-height: 1;
  }

  .brand p {
    font-size: 12px;
    color: var(--muted);
    font-family: 'DM Mono', monospace;
    margin-top: 3px;
  }

  .stats {
    margin-left: auto;
    display: flex; gap: 24px;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: var(--muted);
    text-align: right;
  }

  .stats span { color: var(--accent); font-weight: 500; }

  /* ── Search ── */
  .search-wrap {
    padding: 32px 40px 0;
    max-width: 900px;
  }

  .search-box {
    position: relative;
  }

  .search-box input {
    width: 100%;
    padding: 16px 20px 16px 52px;
    font-size: 17px;
    font-family: 'Syne', sans-serif;
    font-weight: 600;
    background: var(--surface);
    border: 2px solid var(--border);
    border-radius: var(--radius);
    color: var(--text);
    outline: none;
    transition: border-color 0.2s;
  }

  .search-box input:focus {
    border-color: var(--accent);
  }

  .search-box input::placeholder { color: var(--muted); font-weight: 400; }

  .search-icon {
    position: absolute; left: 18px; top: 50%; transform: translateY(-50%);
    color: var(--muted); font-size: 18px; pointer-events: none;
  }

  .hint {
    margin-top: 10px;
    font-size: 12px;
    font-family: 'DM Mono', monospace;
    color: var(--muted);
    padding-left: 4px;
  }

  /* ── Results ── */
  .results {
    padding: 28px 40px 0;
    max-width: 1100px;
    display: flex;
    flex-direction: column;
    gap: 32px;
  }

  .empty-state {
    padding: 60px 40px;
    text-align: center;
    color: var(--muted);
    font-family: 'DM Mono', monospace;
    font-size: 13px;
  }

  /* ── Group ── */
  .group-label {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
  }

  .group-label .tag {
    font-size: 10px;
    font-family: 'DM Mono', monospace;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: 20px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .tag-clinic  { background: rgba(74,158,255,0.15); color: var(--clinic); }
  .tag-doctor  { background: rgba(126,212,160,0.15); color: var(--doctor); }
  .tag-patient { background: rgba(199,146,234,0.15); color: var(--patient); }

  .group-count {
    font-size: 12px;
    font-family: 'DM Mono', monospace;
    color: var(--muted);
  }

  /* ── Table ── */
  .table-wrap {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }

  thead th {
    background: var(--surface);
    padding: 10px 16px;
    text-align: left;
    font-size: 10px;
    font-family: 'DM Mono', monospace;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    font-weight: 500;
  }

  tbody tr {
    border-bottom: 1px solid var(--border);
    transition: background 0.15s;
    cursor: default;
  }

  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: rgba(255,255,255,0.03); }

  td {
    padding: 12px 16px;
    vertical-align: middle;
  }

  .date-cell {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }

  .clinic-cell  { color: var(--clinic);  font-weight: 600; }
  .doctor-cell  { color: var(--doctor);  }
  .patient-cell { color: var(--patient); font-weight: 600; }

  .path-cell {
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    color: var(--muted);
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .copy-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    font-size: 11px;
    font-family: 'DM Mono', monospace;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--muted);
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }

  .copy-btn:hover {
    border-color: var(--accent);
    color: var(--accent);
    background: rgba(245,166,35,0.08);
  }

  .copy-btn.copied {
    border-color: var(--doctor);
    color: var(--doctor);
    background: rgba(126,212,160,0.08);
  }

  .no-data { color: var(--muted); font-style: italic; }

  /* ── Loading ── */
  .loading {
    padding: 40px;
    text-align: center;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: var(--muted);
  }

  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .loading { animation: pulse 1.2s ease-in-out infinite; }
</style>
</head>
<body>

<header>
  <div class="logo">CF</div>
  <div class="brand">
    <h1>CaseFinder</h1>
    <p>Exocad Project Lookup — YC Lab</p>
  </div>
  <div class="stats" id="stats">
    <div><span id="stat-total">—</span><br>projects</div>
    <div><span id="stat-clinics">—</span><br>clinics</div>
    <div><span id="stat-latest">—</span><br>latest</div>
  </div>
</header>

<div class="search-wrap">
  <div class="search-box">
    <span class="search-icon">⌕</span>
    <input
      type="text"
      id="search"
      placeholder="Search by clinic, doctor, or patient name..."
      autocomplete="off"
      autofocus
    >
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

// Load stats on page load
fetch('/api/stats')
  .then(r => r.json())
  .then(d => {
    document.getElementById('stat-total').textContent = d.total.toLocaleString();
    document.getElementById('stat-clinics').textContent = d.clinics.toLocaleString();
    document.getElementById('stat-latest').textContent = d.latest || '—';
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
  const res = await fetch('/api/search?q=' + encodeURIComponent(q));
  const data = await res.json();

  const total = data.clinics.length + data.doctors.length + data.patients.length;
  if (total === 0) {
    resultsEl.innerHTML = '<div class="empty-state">No results found for "' + escHtml(q) + '"</div>';
    return;
  }

  let html = '';
  if (data.clinics.length)  html += renderGroup('clinic',  'Clinic',   data.clinics);
  if (data.doctors.length)  html += renderGroup('doctor',  'Doctor',   data.doctors);
  if (data.patients.length) html += renderGroup('patient', 'Patient',  data.patients);
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
          <thead>
            <tr>
              <th>Date</th>
              <th>Clinic</th>
              <th>Doctor</th>
              <th>Patient</th>
              <th>Folder Path</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(r => renderRow(r)).join('')}
          </tbody>
        </table>
      </div>
    </div>`;
}

function renderRow(r) {
  const path = r.folder_path || '';
  const safeId = 'btn-' + r.id;
  return `
    <tr>
      <td class="date-cell">${r.project_date || '<span class="no-data">—</span>'}</td>
      <td class="clinic-cell">${escHtml(r.clinic || '—')}</td>
      <td class="doctor-cell">${escHtml(r.doctor || '—')}</td>
      <td class="patient-cell">${escHtml(r.patient || '—')}</td>
      <td class="path-cell" title="${escHtml(path)}">${escHtml(path)}</td>
      <td>
        <button class="copy-btn" id="${safeId}" data-path="${escHtml(path)}" onclick="copyPath(this)">
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
    setTimeout(() => {
      btn.innerHTML = '⎘ Copy path';
      btn.classList.remove('copied');
    }, 2000);
  });
}

function escJs(str) {
  return String(str);
}

function escHtml(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

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