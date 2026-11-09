"""云端后台任务：保留期清理 + 每日全网报告。

原生 asyncio 循环代替 RQ + Redis：单租户单机只有两个周期任务，
一个 worker 进程的调度开销比一套队列还小。要横向扩容时再换 RQ。
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from typing import Any

from . import ai, config, store

JANITOR_S = 6 * 3600
REPORT_S = 30 * 60


def prune(retention_days: int | None = None) -> dict[str, int]:
    days = retention_days or config.RETENTION_DAYS
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with store.connect() as conn:
        daily = conn.execute("DELETE FROM telemetry_daily WHERE day < ?", (cutoff,)).rowcount
        adv = conn.execute(
            "DELETE FROM advice WHERE created_at < ?",
            (int(dt.datetime.now().timestamp() * 1000) - days * 86_400_000,)).rowcount
        words = conn.execute("DELETE FROM word_freq WHERE day < ?", (cutoff,)).rowcount
        reports = conn.execute(
            "DELETE FROM reports WHERE received_at < ?",
            (int(dt.datetime.now().timestamp() * 1000) - days * 86_400_000,)).rowcount
    if daily or adv or words:
        store.audit("system", "retention.prune", "cloud",
                    {"daily": daily, "advice": adv, "words": words})
    return {"daily": daily, "advice": adv, "words": words, "reports": reports}


def maybe_daily_report() -> dict[str, Any] | None:
    """24 小时没有新报告就补一份全网解读。没有数据时不生成。"""
    latest = store.reports(1)
    now = store.now_ms()
    if latest and now - latest[0]["created_at"] < 24 * 3600_000:
        return None
    if not store.daily_rows(5):
        return None
    return ai.generate("fleet", None, force_rules=config.AI_MODE == "rules")


class Scheduler:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.ticks = 0
        self.last: dict[str, Any] = {}

    async def _loop(self) -> None:
        last_janitor = 0.0
        while True:
            await asyncio.sleep(REPORT_S)
            try:
                out: dict[str, Any] = {}
                report = await asyncio.to_thread(maybe_daily_report)
                if report:
                    out["report"] = report["id"]
                if asyncio.get_running_loop().time() - last_janitor > JANITOR_S:
                    out["pruned"] = await asyncio.to_thread(prune)
                    last_janitor = asyncio.get_running_loop().time()
                self.last = out
                self.ticks += 1
            except Exception as e:  # noqa: BLE001
                self.last = {"error": str(e)[:200]}

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None


scheduler = Scheduler()
