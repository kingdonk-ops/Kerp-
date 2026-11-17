"""The SAQ side of outbound e-mail (T-0058, diff §4.6): the hook core mail sends through.

Core never imports a module, so this module installs the enqueuer at start-up
(``install``: the API lifespan and the worker call it) through
``app.core.mail.queue.set_mail_enqueuer``.

* After the caller's commit, ``MailDispatcher`` stores each mail's payload (recipient,
  template context, which may hold a link token) under ``kerp:mail:<send id>`` in Redis with
  a TTL, then enqueues ``kerp_send_mail`` on the **interactive** queue with ids only. So the
  token is never in the SAQ job kwargs (SAQ logs those on errors and at DEBUG) nor in the
  database; the payload is deleted once the send is settled.
* ``send_mail_job`` (interactive worker) loads the payload and calls
  ``app.core.mail.service.deliver``. A transient transport error raises ``RetryMail``, and SAQ
  retries with exponential back-off (``MAIL_RETRIES`` attempts, ``MAIL_RETRY_DELAY_S``
  doubling up to ``MAIL_RETRY_MAX_DELAY_S``); the last attempt records ``failed``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol

from saq import Job as SaqJob

from app.core.mail.queue import QueuedMail, set_mail_enqueuer
from app.core.mail.service import Delivery, deliver, fail_unavailable
from app.core.request_context import request_id_var

SEND_MAIL: Final = "kerp_send_mail"
PAYLOAD_PREFIX: Final = "kerp:mail:"
PAYLOAD_TTL_S: Final = 24 * 3600
MAIL_RETRIES: Final = 6  # attempts in total, the first included
MAIL_RETRY_DELAY_S: Final = 15.0
MAIL_RETRY_MAX_DELAY_S: Final = 600.0
MAIL_TIMEOUT_S: Final = 60

logger = logging.getLogger("kerp.jobs")


class AsyncKeyValue(Protocol):
    """The part of ``redis.asyncio.Redis`` the payload store uses."""

    async def set(self, name: str, value: str, ex: int | None = None) -> object: ...

    async def get(self, name: str) -> str | bytes | None: ...

    async def delete(self, *names: str) -> object: ...


class MailQueue(Protocol):
    """The SAQ interactive queue (``saq.queue.redis.RedisQueue``) as this module uses it."""

    redis: Any

    async def enqueue(self, job_or_func: SaqJob) -> SaqJob | None: ...


def payload_key(send_id: uuid.UUID | str) -> str:
    return f"{PAYLOAD_PREFIX}{send_id}"


def saq_job(mail: QueuedMail) -> SaqJob:
    """The SAQ job of ``mail``: ids only; ``ttl=-1`` drops the job record when it finishes."""
    return SaqJob(
        function=SEND_MAIL,
        key=f"mail:{mail.send_id}",
        kwargs={
            "send_id": str(mail.send_id),
            "tenant_id": str(mail.tenant_id),
            "project_id": str(mail.project_id) if mail.project_id else None,
            "request_id": mail.request_id,
        },
        timeout=MAIL_TIMEOUT_S,
        retries=MAIL_RETRIES,
        retry_delay=MAIL_RETRY_DELAY_S,
        retry_backoff=MAIL_RETRY_MAX_DELAY_S,
        ttl=-1,
    )


async def put(queue: MailQueue, mail: QueuedMail) -> None:
    """Store the payload, then enqueue the job (a job never runs without its payload)."""
    store: AsyncKeyValue = queue.redis
    await store.set(payload_key(mail.send_id), json.dumps(mail.to_payload()), ex=PAYLOAD_TTL_S)
    await queue.enqueue(saq_job(mail))


class MailDispatcher:
    """``MailEnqueuer`` for core mail: hands committed mails to ``put`` on ``loop`` from any
    thread (SQLAlchemy's ``after_commit`` runs in FastAPI's worker threads). A failed
    enqueue is logged; its send-log row stays ``queued``."""

    def __init__(self, queue: MailQueue, loop: asyncio.AbstractEventLoop) -> None:
        self.queue = queue
        self.loop = loop
        self._tasks: set[asyncio.Task[None]] = set()

    def __call__(self, mails: Sequence[QueuedMail]) -> None:
        batch = list(mails)
        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._start(batch)
        else:
            self.loop.call_soon_threadsafe(self._start, batch)

    def _start(self, mails: list[QueuedMail]) -> None:
        for mail in mails:
            task = self.loop.create_task(self._put(mail))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _put(self, mail: QueuedMail) -> None:
        try:
            await put(self.queue, mail)
        except Exception:
            logger.exception(
                "mail enqueue failed; the send stays queued",
                extra={"send_id": str(mail.send_id), "request_id": mail.request_id},
            )

    async def drain(self) -> None:
        """Wait for every scheduled enqueue (shutdown, tests). Call on ``loop``.

        Loops on the pending tasks, not on the set: a finished task may still be in it until
        its done callback runs."""
        await asyncio.sleep(0)  # let mails handed over from other threads start
        while pending := [task for task in self._tasks if not task.done()]:
            await asyncio.gather(*pending)
        self._tasks.difference_update([task for task in self._tasks if task.done()])


def install(queue: MailQueue, loop: asyncio.AbstractEventLoop) -> MailDispatcher:
    """Make core mail enqueue on ``queue`` (the interactive queue) from now on."""
    dispatcher = MailDispatcher(queue, loop)
    set_mail_enqueuer(dispatcher)
    return dispatcher


def uninstall() -> None:
    set_mail_enqueuer(None)


async def send_mail_job(
    ctx: Mapping[str, Any],
    *,
    send_id: str,
    tenant_id: str,
    project_id: str | None,
    request_id: str | None,
) -> str:
    """SAQ function: deliver one queued mail (interactive worker)."""
    queue: MailQueue = ctx["mail_queue"]
    delivery: Delivery = ctx["mail_delivery"]
    job: SaqJob = ctx["job"]
    store: AsyncKeyValue = queue.redis
    token = request_id_var.set(request_id)
    try:
        raw = await store.get(payload_key(send_id))
        if raw is None:
            ref = QueuedMail(
                send_id=uuid.UUID(send_id),
                tenant_id=uuid.UUID(tenant_id),
                project_id=uuid.UUID(project_id) if project_id else None,
                template_key="",
                to="",
                locale=None,
                context={},
                request_id=request_id,
            )
            await asyncio.to_thread(fail_unavailable, delivery.db, ref, "payload_expired")
            logger.error("mail payload missing; send failed", extra={"send_id": send_id})
            return "failed"
        mail = QueuedMail.from_payload(json.loads(raw))
        if str(mail.send_id) != send_id or str(mail.tenant_id) != tenant_id:
            raise ValueError("mail payload does not match its job")
        # ``retryable`` once the worker has counted this attempt: attempts < retries.
        final = not job.retryable
        status = await asyncio.to_thread(deliver, delivery, mail, final_attempt=final)
        await store.delete(payload_key(send_id))
        return status
    finally:
        request_id_var.reset(token)


def worker_context(queue: MailQueue, delivery: Delivery) -> dict[str, Any]:
    """What ``send_mail_job`` expects in the SAQ worker context."""
    return {"mail_queue": queue, "mail_delivery": delivery}


__all__ = [
    "MAIL_RETRIES",
    "SEND_MAIL",
    "MailDispatcher",
    "install",
    "payload_key",
    "put",
    "saq_job",
    "send_mail_job",
    "uninstall",
    "worker_context",
]
