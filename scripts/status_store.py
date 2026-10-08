"""Small durable queue and conservative usage accounting, independent of Codex storage."""
import contextlib
import datetime
import json
import pathlib
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo
from status_core import fingerprint


class Store:
    def __init__(self, directory):
        self.directory = pathlib.Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "state.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs(thread_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    due REAL, event_key TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS seen(event_key TEXT PRIMARY KEY, ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS states(thread_id TEXT PRIMARY KEY, last_title TEXT,
                    fingerprint TEXT, decision TEXT NOT NULL DEFAULT '{}', paused INTEGER NOT NULL DEFAULT 0,
                    last_call REAL NOT NULL DEFAULT 0, last_result TEXT);
                CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY, day TEXT NOT NULL, thread_id TEXT NOT NULL,
                    tokens INTEGER NOT NULL, actual INTEGER, ts REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                    thread_id TEXT NOT NULL, result TEXT NOT NULL, error_type TEXT);
            """)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, event, config, now=None):
        now = time.time() if now is None else now
        tid = event.get("session_id")
        if not isinstance(tid, str) or not tid or len(tid) > 200:
            return False
        key = fingerprint({"thread": tid, "turn": event.get("turn_id"),
                           "message": event.get("last_assistant_message", "")})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM seen WHERE event_key=?", (key,)).fetchone():
                return False
            db.execute("INSERT INTO seen VALUES(?,?)", (key, now))
            db.execute("INSERT OR IGNORE INTO states(thread_id) VALUES(?)", (tid,))
            s = db.execute("SELECT * FROM states WHERE thread_id=?", (tid,)).fetchone()
            if s["paused"]:
                return False
            due = max(now + config["debounce_seconds"], s["last_call"] + config["min_interval_seconds"])
            db.execute("INSERT INTO jobs VALUES(?,1,?,?) ON CONFLICT(thread_id) DO UPDATE SET "
                       "revision=revision+1,due=excluded.due,event_key=excluded.event_key", (tid, due, key))
            db.execute("DELETE FROM seen WHERE ts<?", (now - 86400 * 90,))
        return True

    def next_job(self, now=None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE due IS NOT NULL AND due<=? ORDER BY due LIMIT 1",
                             (time.time() if now is None else now,)).fetchone()
            return dict(row) if row else None

    def pending_due(self):
        with self.connect() as db:
            return db.execute("SELECT MIN(due) FROM jobs WHERE due IS NOT NULL").fetchone()[0]

    def current(self, tid):
        with self.connect() as db:
            r = db.execute("SELECT * FROM states WHERE thread_id=?", (tid,)).fetchone()
            if not r:
                return {"last_title": None, "fingerprint": None, "decision": {}, "paused": False}
            s = dict(r)
            s["decision"] = json.loads(s["decision"])
            return s

    def baseline(self, tid, title):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO states(thread_id) VALUES(?)", (tid,))
            db.execute("UPDATE states SET last_title=? WHERE thread_id=? AND last_title IS NULL", (title, tid))

    def is_current(self, tid, revision):
        with self.connect() as db:
            r = db.execute("SELECT revision FROM jobs WHERE thread_id=? AND due IS NOT NULL", (tid,)).fetchone()
            s = db.execute("SELECT paused FROM states WHERE thread_id=?", (tid,)).fetchone()
            return bool(r and r[0] == revision and s and not s[0])

    def reserve(self, tid, config, now=None):
        now = time.time() if now is None else now
        day = datetime.datetime.fromtimestamp(now, ZoneInfo(config["timezone"])).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO states(thread_id) VALUES(?)", (tid,))
            last_call = db.execute("SELECT last_call FROM states WHERE thread_id=?", (tid,)).fetchone()[0]
            if last_call and now < last_call + config["min_interval_seconds"]:
                return None
            count, tokens = db.execute("SELECT COUNT(*),COALESCE(SUM(tokens),0) FROM usage WHERE day=?", (day,)).fetchone()
            per_thread = db.execute("SELECT COUNT(*) FROM usage WHERE day=? AND thread_id=?", (day, tid)).fetchone()[0]
            if count >= config["max_calls_per_day"] or per_thread >= config["max_calls_per_thread_per_day"]:
                return None
            if tokens + config["token_reservation"] > config["max_tokens_per_day"]:
                return None
            rid = str(uuid.uuid4())
            db.execute("INSERT INTO usage VALUES(?,?,?,?,NULL,?)", (rid, day, tid, config["token_reservation"], now))
            db.execute("UPDATE states SET last_call=? WHERE thread_id=?", (now, tid))
            return rid

    def budget_available(self, tid, config, now=None):
        """Cheap preflight; reserve() remains the atomic authority for paid requests."""
        now = time.time() if now is None else now
        day = datetime.datetime.fromtimestamp(now, ZoneInfo(config["timezone"])).date().isoformat()
        with self.connect() as db:
            count, tokens = db.execute("SELECT COUNT(*),COALESCE(SUM(tokens),0) FROM usage WHERE day=?", (day,)).fetchone()
            per_thread = db.execute("SELECT COUNT(*) FROM usage WHERE day=? AND thread_id=?", (day, tid)).fetchone()[0]
            return (count < config["max_calls_per_day"] and per_thread < config["max_calls_per_thread_per_day"]
                    and tokens + config["token_reservation"] <= config["max_tokens_per_day"])

    def settle(self, rid, usage):
        if isinstance(usage, int) and not isinstance(usage, bool) and usage >= 0:
            with self.connect() as db:
                db.execute("UPDATE usage SET tokens=?,actual=? WHERE id=?", (usage, usage, rid))

    def finish(self, tid, revision, fp, decision, title, result="updated"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM jobs WHERE thread_id=?", (tid,)).fetchone()
            if not row or row[0] != revision:
                return False
            db.execute("UPDATE states SET fingerprint=?,decision=?,last_title=COALESCE(?,last_title),last_result=? WHERE thread_id=?",
                       (fp, json.dumps(decision, ensure_ascii=False), title, result, tid))
            db.execute("UPDATE jobs SET due=NULL WHERE thread_id=?", (tid,))
            self._event(db, tid, result)
            return True

    @staticmethod
    def _event(db, tid, result, error_type=None):
        db.execute("INSERT INTO events(ts,thread_id,result,error_type) VALUES(?,?,?,?)",
                   (time.time(), tid, result, error_type))
        db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 200)")

    def discard(self, tid, revision, reason, error_type=None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET due=NULL WHERE thread_id=? AND revision=?", (tid, revision))
            db.execute("UPDATE states SET last_result=? WHERE thread_id=?", (reason, tid))
            self._event(db, tid, reason, error_type)

    def defer(self, tid, revision, due, reason):
        with self.connect() as db:
            db.execute("UPDATE jobs SET due=? WHERE thread_id=? AND revision=?", (due, tid, revision))
            db.execute("UPDATE states SET last_result=? WHERE thread_id=?", (reason, tid))
            self._event(db, tid, reason)

    def events(self, limit=20):
        if not 1 <= limit <= 200:
            raise ValueError("event limit must be 1..200")
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT ts,thread_id,result,error_type FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def pause(self, tid, paused=True):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO states(thread_id) VALUES(?)", (tid,))
            db.execute("UPDATE states SET paused=?,last_title=CASE WHEN ?=0 THEN NULL ELSE last_title END WHERE thread_id=?",
                       (int(paused), int(paused), tid))
            if paused:
                db.execute("UPDATE jobs SET due=NULL WHERE thread_id=?", (tid,))

    def report(self):
        with self.connect() as db:
            return {"usage": [dict(r) for r in db.execute("SELECT day,COUNT(*) calls,SUM(tokens) tokens,"
                    "SUM(CASE WHEN actual IS NULL THEN 1 ELSE 0 END) uncertain_calls FROM usage GROUP BY day ORDER BY day DESC LIMIT 7")],
                    "threads": [dict(r) for r in db.execute("SELECT thread_id,paused,last_result,last_title FROM states")],
                    "pending": db.execute("SELECT COUNT(*) FROM jobs WHERE due IS NOT NULL").fetchone()[0]}
