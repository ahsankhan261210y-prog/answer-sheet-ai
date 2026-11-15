import sqlite3, json, os

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
