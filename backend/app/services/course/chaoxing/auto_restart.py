"""One persisted automatic restart per failed managed task, after one hour."""

import asyncio
import logging
import threading
from datetime import UTC, datetime

from .learning_manager import LEARNING_TASK_KIND, USER_TASK_LOAD_LIMIT, learning_manager
from ..task_store import task_store

logger = logging.getLogger(__name__)
POLL_SECONDS = 30


def process_due_restarts(stop: threading.Event, now: datetime | None = None) -> None:
    from app.api.v1.learning_accounts import restart_saved_task

    now = now or datetime.now(UTC)
    rows = task_store.list_tasks(LEARNING_TASK_KIND, user_id=None, limit=USER_TASK_LOAD_LIMIT, only_auto_restart=True)
    for row in rows:
        if stop.is_set():
            return
        if (row.get("status") != "failed" or row.get("auto_restart_state") != "waiting"
                or row.get("auto_restart_attempts", 0) or not row.get("auto_restart_at")):
            continue
        try:
            due = datetime.fromisoformat(row["auto_restart_at"])
            if due.tzinfo is None or due > now:
                continue
        except (TypeError, ValueError):
            continue
        owner, task_id = str(row.get("user_id") or ""), str(row.get("task_id") or "")
        if not owner or not task_id:
            continue
        try:
            restart_saved_task(task_id, owner, automatic=True, now=now, should_stop=stop.is_set)
        except Exception as exc:
            # Do not echo exceptions that may contain saved provider settings.
            logger.warning("Automatic task restart unavailable (%s)", type(exc).__name__)
            learning_manager._update_task(task_id, auto_restart_state="blocked", auto_restart_at=None)
            learning_manager._append_task_log(task_id, "Automatic restart unavailable; manual action required", "warning")


async def auto_restart_loop() -> None:
    stop = threading.Event()
    try:
        while not stop.is_set():
            run = asyncio.create_task(asyncio.to_thread(process_due_restarts, stop))
            try:
                # Cancellation must stop and join an in-flight scan before the
                # app finishes shutting down, preventing late admissions.
                await asyncio.shield(run)
            except asyncio.CancelledError:
                stop.set()
                await run
                raise
            except Exception as exc:
                logger.warning("Automatic restart scan failed (%s)", type(exc).__name__)
            await asyncio.sleep(POLL_SECONDS)
    finally:
        stop.set()
