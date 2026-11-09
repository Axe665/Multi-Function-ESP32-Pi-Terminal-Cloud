"""worker 侧的任务函数。

文档 §6.1 的队列里只有三件事：AI 报告生成、词频、跨边聚合。
保留期清理与每日备份也由 worker 进程排期，但它们不进队列语义，
只是同一条时间循环里的动作。
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
from typing import Any

from . import config, queue, store


def generate_report(scope: str, scope_id: str | None = None,
                    task: str = "weekly_narrative", mode: str | None = None) -> dict[str, Any]:
    from . import ai, store as _store
    out = ai.generate(scope, scope_id, force_rules=(mode or config.AI_MODE) == "rules", task=task)
    _store.audit("admin", "export.report", f"report:{out['id']}",
                 {"model": out["model"], "scope": scope})
    return out


def rebuild_wordfreq(days: int = 30) -> dict[str, Any]:
    """把最近 n 天的词频汇总进 Redis，页面查词云时先看缓存。"""
    today = dt.date.today()
    scope_word: dict[str, int] = {}
    rows = store.wordcloud(None, days, today.isoformat())
    for r in rows:
        scope_word[r["word"]] = int(r["count"])
    payload = {"generated_at": store.now_ms(), "days": days,
               "words": [{"word": w, "count": c} for w, c in
                         sorted(scope_word.items(), key=lambda kv: -kv[1])[:40]]}
    queue.cache_set(f"words:{days}", payload, ttl_s=3600)
    return {"words": len(payload["words"]), "days": days}


def refresh_rollup() -> dict[str, Any]:
    """跨边聚合：每个边每个指标的均值，供 /compare 先用缓存。"""
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT edge_id, metric, AVG(mean) AS mean, MAX(ABS(z)) AS z,
                      MIN(k) AS k_min, COUNT(DISTINCT device_id) AS devices
               FROM telemetry_daily GROUP BY edge_id, metric""").fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r["edge_id"], {})[r["metric"]] = {
            "mean": round(float(r["mean"] or 0), 3),
            "z": round(float(r["z"] or 0), 2),
            "k_min": int(r["k_min"] or 0),
            "devices": int(r["devices"] or 0),
        }
    queue.cache_set("rollup:edges", {"generated_at": store.now_ms(), "edges": out}, ttl_s=900)
    return {"edges": len(out), "metrics": sum(len(v) for v in out.values())}


def run_backup() -> dict[str, Any]:
    """pg_dump 自定义格式，落 BACKUP_DIR，保留 14 天。"""
    config.ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = config.BACKUP_DIR / f"cloud-{stamp}.dump"
    cmd = ["pg_dump", "--format=custom", "--no-owner", "--file", str(target),
           config.PG_DSN]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        store.audit("system", "backup.fail", "cloud", {"error": res.stderr[-300:]})
        return {"ok": False, "error": res.stderr[-300:]}
    # 保留 14 天
    cutoff = time.time() - 14 * 86400
    removed = 0
    for f in config.BACKUP_DIR.glob("cloud-*.dump"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    size = target.stat().st_size
    store.audit("system", "backup.done", "cloud",
                {"file": target.name, "bytes": size, "pruned": removed})
    return {"ok": True, "file": target.name, "bytes": size, "pruned": removed}


def prune(retention_days: int | None = None) -> dict[str, int]:
    days = retention_days or config.RETENTION_DAYS
    today = dt.date.today()
    cutoff_day = (today - dt.timedelta(days=days)).isoformat()
    cutoff_ms = store.now_ms() - days * 86_400_000
    cutoff = dt.datetime.fromtimestamp(cutoff_ms / 1000.0, tz=dt.timezone.utc)
    with store.connect() as conn:
        daily = conn.execute("DELETE FROM telemetry_daily WHERE day < %s", (cutoff_day,)).rowcount
        adv = conn.execute("DELETE FROM advice WHERE created_at < %s", (cutoff,)).rowcount
        words = conn.execute("DELETE FROM word_freq WHERE day < %s", (cutoff_day,)).rowcount
        reports = conn.execute("DELETE FROM reports WHERE received_at < %s", (cutoff,)).rowcount
    if daily or adv or words:
        store.audit("system", "retention.prune", "cloud",
                    {"daily": daily, "advice": adv, "words": words})
    return {"daily": daily, "advice": adv, "words": words, "reports": reports}


def maybe_daily_report() -> dict[str, Any] | None:
    """24 小时没有新报告就补一份全网解读。没有数据时不生成。"""
    from . import ai
    latest = store.reports(1)
    if latest and store.now_ms() - latest[0]["created_at"] < 24 * 3600_000:
        return None
    if not store.daily_rows(5):
        return None
    return ai.generate("fleet", None, force_rules=config.AI_MODE == "rules")


JOBS = {
    "generate_report": generate_report,
    "rebuild_wordfreq": rebuild_wordfreq,
    "refresh_rollup": refresh_rollup,
    "run_backup": run_backup,
    "prune": prune,
    "maybe_daily_report": maybe_daily_report,
}
