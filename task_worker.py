"""Background task worker using threading.

Polls the DB for queued tasks and processes them sequentially.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0


_STALE_TASK_MINUTES = 30


class TaskWorker:
    def __init__(self, app):
        self._app = app
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._recover_stale_tasks()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="task-worker")
        self._thread.start()
        logger.info("TaskWorker started")

    def _recover_stale_tasks(self) -> None:
        """Reset ALL running tasks to queued on startup — they can't be running if the process just started."""
        with self._app.app_context():
            from models import Task, db

            stale = Task.query.filter_by(status="running").all()
            if stale:
                for t in stale:
                    t.status = "queued"
                    t.progress = 0
                    t.progress_msg = "Recovered after service restart"
                    t.started_at = None
                db.session.commit()
                logger.info("Recovered %d interrupted tasks back to queued on startup", len(stale))

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("TaskWorker stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                logger.exception("TaskWorker poll error")
            self._stop_event.wait(_POLL_INTERVAL)

    def _poll_once(self) -> None:
        with self._app.app_context():
            from models import Task, Repo, User, db
            from utils import list_raw_sources, list_wiki_pages, get_repo_path
            from config import Config
            import datetime as dt

            cutoff = datetime.now(timezone.utc) - dt.timedelta(minutes=_STALE_TASK_MINUTES)
            stale_count = (
                Task.query
                .filter_by(status="running")
                .filter(Task.started_at < cutoff)
                .update({"status": "failed", "progress_msg": f"Timeout after {_STALE_TASK_MINUTES} min",
                         "finished_at": datetime.now(timezone.utc)})
            )
            if stale_count:
                db.session.commit()
                logger.warning("Marked %d stale running tasks as failed", stale_count)

            task = (
                Task.query
                .filter_by(status="queued")
                .order_by(Task.created_at.asc())
                .first()
            )
            if not task:
                return

            # Optimistic lock: only claim if still queued
            rows = (
                db.session.query(Task)
                .filter_by(id=task.id, status="queued")
                .update({"status": "running", "started_at": datetime.now(timezone.utc),
                         "progress": 0, "progress_msg": "Starting..."})
            )
            db.session.commit()
            if rows == 0:
                return  # another worker claimed it

            db.session.refresh(task)
            repo = db.session.get(Repo, task.repo_id)
            owner = db.session.get(User, repo.user_id)
            if not repo or not owner:
                task.status = "failed"
                task.progress_msg = "Repo or owner not found"
                task.finished_at = datetime.now(timezone.utc)
                db.session.commit()
                return

            logger.info(
                "TaskWorker picked task %d: type=%s repo=%s/%s file=%s",
                task.id, task.type, owner.username, repo.slug, task.input_data,
            )

            try:
                if task.type == "ingest":
                    self._run_ingest(task, repo, owner)
                elif task.type == "rebuild_index":
                    self._run_rebuild_index(task, repo, owner)
                else:
                    task.status = "failed"
                    task.progress_msg = f"Unknown task type: {task.type}"
                    task.finished_at = datetime.now(timezone.utc)
                    db.session.commit()
            except Exception as exc:
                logger.exception("Task %d failed", task.id)
                task.status = "failed"
                task.progress_msg = str(exc)[:2000]
                task.finished_at = datetime.now(timezone.utc)
                db.session.commit()

    def _run_ingest(self, task, repo, owner) -> None:
        from models import db
        from utils import list_raw_sources, list_wiki_pages, get_repo_path
        from config import Config
        import os

        wiki_engine = self._app.wiki_engine

        for event in wiki_engine.ingest(repo, owner.username, task.input_data):
            phase = event.get("phase", "")
            progress = event.get("progress", 0)
            message = event.get("message", "")

            task.progress = progress
            task.progress_msg = message
            db.session.commit()

            if phase == "error":
                task.status = "failed"
                task.progress_msg = message
                task.finished_at = datetime.now(timezone.utc)
                db.session.commit()
                return

        task.status = "done"
        task.progress = 100
        task.finished_at = datetime.now(timezone.utc)
        db.session.commit()

        base = get_repo_path(Config.DATA_DIR, owner.username, repo.slug)
        repo.source_count = len(list_raw_sources(os.path.join(base, "raw")))
        repo.page_count = len(list_wiki_pages(os.path.join(base, "wiki")))
        db.session.commit()

        logger.info("Task %d completed: %s", task.id, task.progress_msg)

    def _run_rebuild_index(self, task, repo, owner) -> None:
        """重建 Qdrant page + chunk 索引，用于 import_zip 后回填。"""
        from models import db
        from utils import list_wiki_pages, get_repo_path
        from config import Config
        import os

        base = get_repo_path(Config.DATA_DIR, owner.username, repo.slug)
        wiki_dir = os.path.join(base, "wiki")

        if not os.path.isdir(wiki_dir):
            task.status = "done"
            task.progress_msg = "wiki dir not found"
            task.finished_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            db.session.commit()
            return

        pages = list_wiki_pages(wiki_dir)
        rebuilt = 0
        qdrant = self._app.qdrant
        for page in pages:
            fpath = os.path.join(wiki_dir, page["filename"])
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                if qdrant:
                    qdrant.upsert_page(
                        repo_id=repo.id, filename=page["filename"],
                        title=page["title"], page_type=page["type"], content=content,
                    )
                    qdrant.upsert_page_chunks(
                        repo_id=repo.id, filename=page["filename"],
                        title=page["title"], page_type=page["type"], content=content,
                    )
                rebuilt += 1
            except Exception as exc:
                logger.warning("rebuild_index failed for %s: %s", page["filename"], exc)

        task.status = "done"
        task.progress_msg = f"Rebuilt index for {rebuilt}/{len(pages)} pages"
        from datetime import datetime, timezone
        task.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.info("Task %d rebuild_index done: %d/%d", task.id, rebuilt, len(pages))
