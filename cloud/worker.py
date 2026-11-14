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
    last = {"rollup": 0.0, "words": 0.0, "report": 0.0, "prune": 0.0, "backup": 0.0, "drill": 0.0,
            "beat": 0.0}
    while True:
        try:
            now = _now()
            if now - last["rollup"] > 600:
                jobs.refresh_rollup(); last["rollup"] = now
            if now - last["words"] > 600:
                jobs.rebuild_wordfreq(); last["words"] = now
            if now - last["beat"] > 900:
                jobs.demo_heartbeat(); last["beat"] = now
            if now - last["report"] > 1800:
                jobs.maybe_daily_report(); last["report"] = now
            if now - last["prune"] > 6 * 3600:
                jobs.prune(); last["prune"] = now
            # 每天 03:00 一次全量备份。判据用审计表，不用「距上次 20 小时」：
            # 后者会让时刻每天往后漂（03:00 → 23:00 → 19:00 …）
            today = dt.date.today()
            at_three = dt.datetime.combine(today, dt.time(3, 0)).timestamp()
            if now >= at_three:
                if not store.audit_since("backup.done", int(at_three * 1000)):
                    jobs.run_backup()
                # 文档 §6.7：每周一份加密离线副本。到期（周日，或上次成功已过一周）
                # 且今天还没试过才做——当天失败隔天会再试，别等下一个周日
                if _encrypt_due(at_three) and not _encrypted_today(at_three):
                    _encrypt_latest()
            month_start = dt.datetime.combine(today.replace(day=1), dt.time(0, 0)).timestamp()
            # 每月一次恢复演练；失败就隔天再试（当天只试一次，成功后本月不再做）
            if not _drilled(month_start) and not _drill_tried_today(at_three):
                jobs.run_restore_drill()
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


def _encrypt_due(at_three: float) -> bool:
    """加密副本到期了没：周日，或上一次成功已经超过一周（上次失败过就得补）。"""
    if dt.date.today().weekday() == 6:
        return True
    return not store.audit_since("backup.encrypt", int((at_three - 6 * 86400) * 1000))


def _encrypted_today(at_three: float) -> bool:
    """今天已经试过（成功或失败）——失败只在当天算，隔天重试。"""
    since = int(at_three * 1000)
    return (store.audit_since("backup.encrypt", since)
            or store.audit_since("backup.encrypt_fail", since))


def _encrypt_latest() -> None:
    dumps = sorted(config.BACKUP_DIR.glob("cloud-*.dump"), key=lambda f: f.stat().st_mtime)
    if dumps:
        jobs._encrypt(dumps[-1])


def _drilled(month_start: float) -> bool:
    """本月演练成功过。失败不算：演练失败恰恰是第二天要继续试的理由。"""
    return store.audit_since("backup.drill", int(month_start * 1000))


def _drill_tried_today(at_three: float) -> bool:
    """今天已经试过演练（成功或失败），免得每分钟重跑一次导入。"""
    since = int(at_three * 1000)
    return (store.audit_since("backup.drill", since)
            or store.audit_since("backup.drill_fail", since))
