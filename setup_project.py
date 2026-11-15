import os

BASE = "answer-sheet-ai"

FILES = {}

FILES["backend/requirements.txt"] = r'''fastapi==0.115.0
uvicorn==0.30.6
python-multipart==0.0.9
python-dotenv==1.0.1
anthropic>=0.40.0
'''

FILES["backend/.env"] = r'''ANTHROPIC_API_KEY=yahan_apni_key_daalein
MODEL=claude-sonnet-5-5
'''

FILES["backend/uploads/.gitkeep"] = ""

FILES["backend/database.py"] = r'''import sqlite3, json, os

DB_PATH = os.path.join(os.path.dirname(__file__), "grading.db")


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS exams(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            questions TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            student_name TEXT NOT NULL,
            image_path TEXT,
            extracted_text TEXT,
            result TEXT,
            total REAL,
            max_total REAL,
            status TEXT DEFAULT 'graded',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS rechecks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            reason TEXT,
            old_total REAL,
            new_total REAL,
            result TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)


def row(r):
    d = dict(r)
    for k in ("questions", "result"):
        if k in d and d[k]:
            d[k] = json.loads(d[k])
    return d
'''

FILES["backend/ocr.py"] = r'''import base64, os, mimetypes
from anthropic import Anthropic

_client = None


def client():
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def extract_text(path):
    media = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    msg = client().messages.create(
        model=os.getenv("MODEL", "claude-sonnet-5-5"),
        max_tokens=3000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                {"type": "text", "text": "Transcribe all text on this answer sheet exactly as written, keeping question numbers. Output only the transcription."},
            ],
        }],
    )
    return msg.content[0].text
'''

FILES["backend/grader.py"] = r'''import json, os, re
from ocr import client

SYSTEM = (
    "You are a fair, consistent examiner. Grade the student's answer sheet against the "
    "model answers. Give partial marks for partially correct answers. "
    "Reply with ONLY valid JSON, no markdown."
)


def grade(questions, student_text, recheck_reason=None):
    prompt = {
        "questions": questions,
        "student_answer_sheet": student_text,
        "output_format": {
            "results": [{"q_no": 1, "awarded": 0, "max": 0, "feedback": "short reason"}],
            "total": 0,
            "max_total": 0,
        },
    }
    if recheck_reason:
        prompt["recheck_request"] = (
            f"The student asked for a recheck: '{recheck_reason}'. Re-evaluate carefully "
            "and independently. Only change marks if the original grading was truly wrong."
        )
    msg = client().messages.create(
        model=os.getenv("MODEL", "claude-sonnet-5-5"),
        max_tokens=3000,
        system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
    )
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    data = json.loads(text)
    data["total"] = sum(r["awarded"] for r in data["results"])
    data["max_total"] = sum(r["max"] for r in data["results"])
    return data
'''

FILES["backend/main.py"] = r'''import json, os, shutil, uuid
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()
import database as db
from ocr import extract_text
from grader import grade

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
os.makedirs(UPLOADS, exist_ok=True)
db.init_db()

app = FastAPI(title="AI Answer Sheet Checker")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Question(BaseModel):
    q: str
    answer: str
    marks: float


class ExamIn(BaseModel):
    title: str
    questions: list[Question]


class RecheckIn(BaseModel):
    reason: str


class OverrideIn(BaseModel):
    total: float


@app.post("/api/exams")
def create_exam(e: ExamIn):
    with db.conn() as c:
        cur = c.execute("INSERT INTO exams(title, questions) VALUES(?,?)",
                        (e.title, json.dumps([q.model_dump() for q in e.questions])))
        return {"id": cur.lastrowid}


@app.get("/api/exams")
def list_exams():
    with db.conn() as c:
        return [db.row(r) for r in c.execute("SELECT * FROM exams ORDER BY id DESC")]


@app.post("/api/submit")
async def submit(exam_id: int = Form(...), student_name: str = Form(...), file: UploadFile = File(...)):
    with db.conn() as c:
        exam = c.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
    if not exam:
        raise HTTPException(404, "Exam nahi mila")
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    path = os.path.join(UPLOADS, f"{uuid.uuid4().hex}{ext}")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        text = extract_text(path)
        result = grade(json.loads(exam["questions"]), text)
    except Exception as ex:
        raise HTTPException(500, f"Checking nakam hui: {ex}")
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO submissions(exam_id, student_name, image_path, extracted_text, result, total, max_total)"
            " VALUES(?,?,?,?,?,?,?)",
            (exam_id, student_name, path, text, json.dumps(result), result["total"], result["max_total"]))
        sid = cur.lastrowid
    return {"id": sid, "extracted_text": text, **result}


@app.get("/api/submissions")
def submissions():
    with db.conn() as c:
        rows = c.execute("""SELECT s.*, e.title AS exam_title FROM submissions s
                            JOIN exams e ON e.id = s.exam_id ORDER BY s.id DESC""").fetchall()
        return [db.row(r) for r in rows]


@app.post("/api/recheck/{sid}")
def recheck(sid: int, body: RecheckIn):
    with db.conn() as c:
        s = c.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "Sheet nahi mili")
        exam = c.execute("SELECT * FROM exams WHERE id=?", (s["exam_id"],)).fetchone()
    try:
        new = grade(json.loads(exam["questions"]), s["extracted_text"], body.reason)
    except Exception as ex:
        raise HTTPException(500, f"Recheck nakam hua: {ex}")
    with db.conn() as c:
        c.execute("INSERT INTO rechecks(submission_id, reason, old_total, new_total, result) VALUES(?,?,?,?,?)",
                  (sid, body.reason, s["total"], new["total"], json.dumps(new)))
        c.execute("UPDATE submissions SET result=?, total=?, status='rechecked' WHERE id=?",
                  (json.dumps(new), new["total"], sid))
    return {"old_total": s["total"], "new_total": new["total"], **new}


@app.put("/api/submissions/{sid}/override")
def override(sid: int, body: OverrideIn):
    with db.conn() as c:
        c.execute("UPDATE submissions SET total=?, status='teacher_override' WHERE id=?", (body.total, sid))
    return {"ok": True}


app.mount("/", StaticFiles(directory=os.path.join(BASE, "..", "frontend"), html=True), name="frontend")
'''

FILES["frontend/index.html"] = r'''<!DOCTYPE html>
<html lang="ur-Latn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Answer Sheet Checker</title>
<style>
  *{box-sizing:border-box}
  body{font-family:system-ui,sans-serif;margin:0;background:#f4f6fa;color:#222}
  header{background:#1f3a8a;color:#fff;padding:16px 20px;font-size:1.2rem}
  nav{display:flex;gap:8px;padding:12px 20px;background:#fff;border-bottom:1px solid #ddd;flex-wrap:wrap}
  nav button{padding:8px 14px;border:0;border-radius:6px;background:#e5e7eb;cursor:pointer}
  nav button.on{background:#1f3a8a;color:#fff}
  main{max-width:900px;margin:20px auto;padding:0 16px}
  section{display:none;background:#fff;padding:18px;border-radius:10px;box-shadow:0 1px 4px #0001}
  section.on{display:block}
  input,select,textarea{width:100%;padding:9px;margin:6px 0 12px;border:1px solid #ccc;border-radius:6px;font:inherit}
  textarea{min-height:140px}
  button.act{background:#1f3a8a;color:#fff;border:0;padding:10px 16px;border-radius:6px;cursor:pointer;margin-right:6px}
  .card{border:1px solid #e5e7eb;border-radius:8px;padding:12px;margin:10px 0}
  .muted{color:#666;font-size:.9rem}
  pre{white-space:pre-wrap;background:#f9fafb;padding:8px;border-radius:6px}
</style>
</head>
<body>
<header>🎓 AI Answer Sheet Checking aur Rechecking System</header>
<nav>
  <button class="on" data-t="exam">1. Exam Banayein</button>
  <button data-t="submit">2. Sheet Upload Karein</button>
  <button data-t="results">3. Nateeje / Recheck</button>
</nav>
<main>
  <section id="exam" class="on">
    <label>Exam ka naam</label>
    <input id="title" placeholder="masalan: Database Systems Midterm">
    <label>Sawalat (har line mein ek): <b>sawal | sahi jawab | marks</b></label>
    <textarea id="qs" placeholder="Normalization kya hai? | Data ki redundancy kam karne ka tareeqa | 5&#10;Primary key kya hai? | Har row ki unique pehchan | 3"></textarea>
    <button class="act" onclick="createExam()">Exam Save Karein</button>
    <p id="examMsg" class="muted"></p>
  </section>

  <section id="submit">
    <label>Exam</label><select id="examSel"></select>
    <label>Student ka naam</label><input id="student">
    <label>Answer sheet ki tasveer (JPG/PNG)</label>
    <input type="file" id="file" accept="image/*">
    <button class="act" onclick="submitSheet()">Abhi Check Karein</button>
    <p id="subMsg" class="muted"></p>
    <div id="subOut"></div>
  </section>

  <section id="results">
    <button class="act" onclick="loadResults()">Refresh</button>
    <div id="list"></div>
  </section>
</main>

<script>
const $ = id => document.getElementById(id);
document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("nav button,section").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); $(b.dataset.t).classList.add("on");
  if (b.dataset.t === "submit") loadExams();
  if (b.dataset.t === "results") loadResults();
});

async function createExam() {
  const questions = $("qs").value.split("\n").map(l => l.split("|").map(s => s.trim()))
    .filter(p => p.length === 3).map(p => ({q: p[0], answer: p[1], marks: parseFloat(p[2])}));
  if (!$("title").value || !questions.length) return $("examMsg").textContent = "Exam ka naam aur durust sawalat zaroori hain.";
  const r = await fetch("/api/exams", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({title: $("title").value, questions})});
  $("examMsg").textContent = r.ok ? "✅ Exam save ho gaya!" : "❌ Exam save nahi hua";
}

async function loadExams() {
  const exams = await (await fetch("/api/exams")).json();
  $("examSel").innerHTML = exams.map(e => `<option value="${e.id}">${e.title}</option>`).join("");
}

async function submitSheet() {
  if (!$("file").files[0] || !$("student").value) return $("subMsg").textContent = "Naam aur file zaroori hain.";
  const fd = new FormData();
  fd.append("exam_id", $("examSel").value);
  fd.append("student_name", $("student").value);
  fd.append("file", $("file").files[0]);
  $("subMsg").textContent = "⏳ Sheet padhi ja rahi hai aur check ho rahi hai... intezar karein";
  const r = await fetch("/api/submit", {method: "POST", body: fd});
  const d = await r.json();
  if (!r.ok) return $("subMsg").textContent = "❌ " + (d.detail || "Masla aa gaya");
  $("subMsg").textContent = "";
  $("subOut").innerHTML = renderResult(d) + `<details><summary>Nikala gaya text</summary><pre>${d.extracted_text}</pre></details>`;
}

function renderResult(d) {
  return `<div class="card"><b>Kul marks: ${d.total} / ${d.max_total}</b>` +
    d.results.map(x => `<p>Sawal ${x.q_no}: <b>${x.awarded}/${x.max}</b><br><span class="muted">${x.feedback}</span></p>`).join("") + "</div>";
}

async function loadResults() {
  const subs = await (await fetch("/api/submissions")).json();
  $("list").innerHTML = subs.map(s => `
    <div class="card">
      <b>${s.student_name}</b> — ${s.exam_title}<br>
      Marks: <b>${s.total}/${s.max_total}</b> <span class="muted">[${s.status}]</span>
      <details><summary>Tafseel</summary>${s.result ? renderResult(s.result) : ""}</details>
      <input id="r${s.id}" placeholder="Recheck ki wajah (masalan: Sawal 2 ka jawab durust tha)">
      <button class="act" onclick="recheck(${s.id})">Recheck Mangein</button>
      <button class="act" style="background:#b45309" onclick="override(${s.id})">Teacher Override</button>
      <p id="m${s.id}" class="muted"></p>
    </div>`).join("") || "<p>Abhi koi sheet jama nahi hui.</p>";
}

async function recheck(id) {
  const reason = $("r" + id).value;
  if (!reason) return $("m" + id).textContent = "Pehle wajah likhein.";
  $("m" + id).textContent = "⏳ Dobara check ho raha hai...";
  const r = await fetch("/api/recheck/" + id, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({reason})});
  const d = await r.json();
  $("m" + id).textContent = r.ok ? `Purane: ${d.old_total} → Naye: ${d.new_total}` : "❌ " + d.detail;
  if (r.ok) setTimeout(loadResults, 1500);
}

async function override(id) {
  const v = prompt("Aakhri marks likhein:");
  if (v === null || isNaN(parseFloat(v))) return;
  await fetch(`/api/submissions/${id}/override`, {method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({total: parseFloat(v)})});
  loadResults();
}
</script>
</body>
</html>
'''

FILES["run.bat"] = r'''@echo off
cd backend
if not exist venv python -m venv venv
call venv\Scripts\activate
pip install -r requirements.txt
start http://127.0.0.1:8000
python -m uvicorn main:app --reload
pause
'''

FILES["README.md"] = r'''# AI Answer Sheet Checking aur Rechecking System

1. backend/.env mein apni ANTHROPIC_API_KEY daalein
2. run.bat par double click karein
3. Browser mein http://127.0.0.1:8000 kholein
'''

for rel, content in FILES.items():
    path = os.path.join(BASE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    print("bana:", path)

print("\nProject tayyar hai. backend/.env mein apni API key daalein, phir run.bat chalayein.")