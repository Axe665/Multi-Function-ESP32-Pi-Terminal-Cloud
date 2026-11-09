"""worker 进程：RQ 消费者 + 定时任务排期。

docker compose 里的 `worker` 服务就跑这个。排期在自己进程里，不额外
引入 rq-scheduler，少一个容器、少一份状态。
"""
from __future__ import annotations

import datetime as dt
import threading
import time

from . import config, jobs, queue, store


def _now() -> float:
    return time.time()


def _scheduler_loop() -> None:
    last = {"rollup": 0.0, "words": 0.0, "report": 0.0, "prune": 0.0, "backup": 0.0, "drill": 0.0}
    while True:
        try:
            now = _now()
            if now - last["rollup"] > 600:
                jobs.refresh_rollup(); last["rollup"] = now
            if now - last["words"] > 600:
                jobs.rebuild_wordfreq(); last["words"] = now
            if now - last["report"] > 1800:
                jobs.maybe_daily_report(); last["report"] = now
            if now - last["prune"] > 6 * 3600:
                jobs.prune(); last["prune"] = now
            # 每天 03:00 一次全量备份（重启后当天已备份过就不再补）
            today = dt.date.today()
            at_three = dt.datetime.combine(today, dt.time(3, 0)).timestamp()
            if now >= at_three and now - last["backup"] > 20 * 3600:
                out = jobs.run_backup()
                # 文档 §6.7：每周再做一份加密离线副本；每月做一次恢复演练
                if today.weekday() == 6:
                    jobs._encrypt(config.BACKUP_DIR / out["file"]) if out.get("ok") else None
                last["backup"] = now
            if today.day == 1 and now - last["drill"] > 24 * 3600:
                jobs.run_restore_drill(); last["drill"] = now
        except Exception as e:  # noqa: BLE001 —— 排期线程不能死
            store.audit("system", "worker.schedule_error", "cloud", {"error": str(e)[:200]})
        time.sleep(60)


def main() -> None:
    from rq import Worker

    store.init_db()
    threading.Thread(target=_scheduler_loop, name="schedule", daemon=True).start()
    worker = Worker([queue.QUEUE_NAME], connection=queue.redis_conn())
    print(f"worker: 队列 {queue.QUEUE_NAME} · Redis {config.REDIS_URL}", flush=True)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
