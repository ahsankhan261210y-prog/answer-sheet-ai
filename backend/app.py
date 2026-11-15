import json, os, shutil, uuid
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(override=True)
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
