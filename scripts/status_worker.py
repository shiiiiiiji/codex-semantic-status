"""Background only: classification failure never reaches the original conversation."""
import contextlib
import fcntl
import json
import os
import pathlib
import subprocess
import sys
import time
from codex_rpc import CodexRPC
from status_core import context_payload, load_config, managed_title, validate_decision
from status_store import Store
from provider_config import effective_budget


@contextlib.contextmanager
def file_lock(directory, name, blocking=True):
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / name).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield None
            return
        try:
            yield lock
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def process_job(store, rpc, job, config, now=None):
    budget = effective_budget(config)
    now = time.time() if now is None else now
    tid, revision = job["thread_id"], job["revision"]
    if not config["enabled"]:
        store.discard(tid, revision, "disabled")
        return "disabled"
    if not store.is_current(tid, revision):
        return "stale"
    snap = rpc.snapshot(tid)
    title = snap["title"]
    state = store.current(tid)
    if state["last_title"] is not None and title != state["last_title"]:
        store.pause(tid)
        store.discard(tid, revision, "manual_title")
        return "manual_title"
    if managed_title(title, "in_progress") is None:
        store.discard(tid, revision, "protected_title")
        return "protected_title"
    store.baseline(tid, title)
    if not snap["ready"]:
        store.discard(tid, revision, "active_or_empty")
        return "active_or_empty"
    if snap["fingerprint"] == state["fingerprint"]:
        store.discard(tid, revision, "unchanged")
        return "unchanged"
    rid = store.reserve(tid, budget, now)
    if rid is None:
        store.discard(tid, revision, "budget")
        return "budget"
    payload = context_payload(snap["goal"], snap["messages"], state["decision"], config["max_input_chars"])
    try:
        raw, usage = rpc.classify(payload, config)
        store.settle(rid, usage)
    except Exception as error:
        observed = getattr(error, "usage", None)
        if isinstance(observed, int) and observed >= 0:
            store.settle(rid, max(budget["token_reservation"], observed))
        store.discard(tid, revision, "classification_failed", type(error).__name__)
        return "classification_failed"
    decision = validate_decision(raw, config, not json.loads(payload)["incomplete_user_context"])
    latest = rpc.snapshot(tid)
    if not store.is_current(tid, revision):
        return "stale"
    if latest["title"] != title:
        store.pause(tid)
        store.discard(tid, revision, "manual_title")
        return "manual_title"
    if latest["version"] != snap["version"]:
        due = max(now + config["debounce_seconds"],
                  store.current(tid)["last_call"] + config["min_interval_seconds"])
        store.defer(tid, revision, due, "stale")
        return "stale"
    if not decision:
        store.finish(tid, revision, snap["fingerprint"], state["decision"], title, "uncertain")
        return "uncertain"
    new_title = managed_title(title, decision["state"])
    if new_title != title:
        rpc.set_name(tid, new_title)
        # Record our own write even if another event arrives between the RPC and finish.
        with store.connect() as db:
            db.execute("UPDATE states SET last_title=? WHERE thread_id=?", (new_title, tid))
    store.finish(tid, revision, snap["fingerprint"], decision, new_title)
    return "updated"


def spawn_worker(directory):
    env = dict(os.environ, SEMANTIC_STATUS_DATA=str(directory))
    subprocess.Popen([sys.executable, str(pathlib.Path(__file__).with_name("semantic_status.py")), "worker"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, start_new_session=True, close_fds=True)


def enqueue_hook(event, directory):
    if os.environ.get("CODEX_SEMANTIC_STATUS_CLASSIFIER") == "1":
        return False
    cfg = load_config(directory)
    if not cfg["enabled"] or event.get("hook_event_name") != "Stop" or event.get("stop_hook_active"):
        return False
    with file_lock(directory, "lifecycle.lock"):
        store = Store(directory)
        if store.enqueue(event, cfg):
            spawn_worker(directory)
            return True
    return False


def run_worker(directory):
    # Lock ordering avoids losing an enqueue while the previous worker is exiting.
    with file_lock(directory, "worker.lock", blocking=False) as worker_lock:
        if worker_lock is None:
            return
        store = Store(directory)
        while True:
            cfg = load_config(directory)
            job = store.next_job()
            if job:
                if not cfg["enabled"]:
                    store.discard(job["thread_id"], job["revision"], "disabled")
                    continue
                if not store.budget_available(job["thread_id"], effective_budget(cfg)):
                    store.discard(job["thread_id"], job["revision"], "budget")
                    continue
                try:
                    with CodexRPC(cfg["codex_command"]) as rpc:
                        process_job(store, rpc, job, cfg)
                except Exception as e:
                    store.discard(job["thread_id"], job["revision"], "worker_error", type(e).__name__)
                continue
            with file_lock(directory, "lifecycle.lock"):
                due = store.pending_due()
                if due is None:
                    fcntl.flock(worker_lock, fcntl.LOCK_UN)
                    return
            time.sleep(min(1, max(.05, due - time.time())))
