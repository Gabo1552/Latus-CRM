"""Railway process entrypoint for Latus CRM scheduled automation."""
from __future__ import annotations

import asyncio
import logging
import os
import signal

import server
from automation_worker import ensure_worker_indexes, run_scheduled_cycle, worker_identity


logger = logging.getLogger("latus.automation-worker")


def _int_env(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


async def run_worker() -> None:
    server.validate_environment_guardrails()
    await server._bootstrap_tenant_data()
    await ensure_worker_indexes(server)

    poll_seconds = _int_env("AUTOMATION_POLL_SECONDS", 30, 5)
    interval_seconds = _int_env("AUTOMATION_INTERVAL_SECONDS", 300, 10)
    lease_seconds = _int_env("AUTOMATION_LEASE_SECONDS", 900, 30)
    run_once = (os.environ.get("AUTOMATION_RUN_ONCE") or "").strip().lower() in {"1", "true", "yes", "on"}
    owner = worker_identity()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signame, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows / embedded loops
            pass

    logger.info(
        "Automation worker ready owner=%s poll=%ss interval=%ss lease=%ss",
        owner, poll_seconds, interval_seconds, lease_seconds,
    )
    while not stop.is_set():
        try:
            result = await run_scheduled_cycle(
                server,
                owner=owner,
                interval_seconds=interval_seconds,
                lease_seconds=lease_seconds,
            )
            if result.get("claimed"):
                logger.info("Automation cycle completed ok=%s", result.get("ok"))
        except Exception:
            logger.exception("Automation cycle failed")
        if run_once:
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            pass


def main() -> None:
    try:
        asyncio.run(run_worker())
    finally:
        server._DBProxy.close()


if __name__ == "__main__":
    main()
