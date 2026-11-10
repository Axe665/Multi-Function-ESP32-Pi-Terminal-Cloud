"""worker 侧的任务函数。

文档 §6.1 的队列里只有三件事：AI 报告生成、词频、跨边聚合。
保留期清理与每日备份也由 worker 进程排期，但它们不进队列语义，
只是同一条时间循环里的动作。
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import tempfile
import time

import psycopg
import psycopg.conninfo
from psycopg.rows import dict_row
from typing import Any

from . import config, queue, store


def generate_report(scope: str, scope_id: str | None = None,
                    task: str = "weekly_narrative", mode: str | None = None,
                    period: list[str] | None = None) -> dict[str, Any]:
    from . import ai, store as _store
    rng = (period[0], period[1]) if period else None
    out = ai.generate(scope, scope_id, force_rules=(mode or config.AI_MODE) == "rules",
                      task=task, period=rng)
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


def _encrypt(path) -> dict[str, Any]:
    """每周的离线副本加密（age 不在镜像里，用 gpg 对称加密）。"""
    key_file = config.BACKUP_KEY_FILE
    if not key_file:
        return {"ok": False, "error": "没有配置 CLOUD_BACKUP_KEY_FILE"}
    target = path.with_suffix(path.suffix + ".gpg")
    # worker 以非 root 跑，HOME 不可写；给 gpg 一个临时 homedir
    home = tempfile.mkdtemp(prefix="gpg-")
    res = subprocess.run(
        ["gpg", "--homedir", home, "--batch", "--yes", "--symmetric", "--cipher-algo", "AES256",
         "--passphrase-file", str(key_file), "--output", str(target), str(path)],
        capture_output=True, text=True, timeout=600)
    shutil.rmtree(home, ignore_errors=True)
    if res.returncode != 0:
        return {"ok": False, "error": res.stderr[-200:]}
    return {"ok": True, "file": target.name, "bytes": target.stat().st_size}


def run_restore_drill() -> dict[str, Any]:
    """每月一次的恢复演练：新库导入最新 dump，校验行数与最新日期，然后删掉。"""
    dumps = sorted(config.BACKUP_DIR.glob("cloud-*.dump"), key=lambda f: f.stat().st_mtime)
    if not dumps:
        return {"ok": False, "error": "没有可用的备份文件"}
    newest = dumps[-1]
    started = time.time()
    name = f"cloud_drill_{time.strftime('%Y%m%d%H%M%S')}"
    admin = psycopg.conninfo.make_conninfo(config.PG_DSN)
    drill_dsn = psycopg.conninfo.make_conninfo(config.PG_DSN, dbname=name)
    try:
        with psycopg.connect(admin, autocommit=True, row_factory=dict_row) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        res = subprocess.run(["pg_restore", "--no-owner", "--no-acl", "--dbname", drill_dsn, str(newest)],
                             capture_output=True, text=True, timeout=1800)
        # 客户端版本比服务端新时会带一句 SET transaction_timeout，服务端拒绝但数据照常恢复；
        # 真正的判定是行数与最新日期能不能对上，不是 pg_restore 的退出码。
        if res.returncode not in (0, 1):
            return {"ok": False, "error": (res.stderr or res.stdout)[-300:]}
        with psycopg.connect(drill_dsn, row_factory=dict_row) as conn:
            counts = {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                      for t in ("edges", "devices", "telemetry_daily")}
            latest = conn.execute("SELECT MAX(day) AS d FROM telemetry_daily").fetchone()["d"]
        with psycopg.connect(config.PG_DSN, row_factory=dict_row) as conn:
            source = {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                      for t in ("edges", "devices", "telemetry_daily")}
        missing = {t: (source[t], counts[t]) for t in counts if counts[t] < source[t]}
        if missing:
            return {"ok": False, "error": f"恢复后行数不足：{missing}", "counts": counts}
        took = round(time.time() - started, 1)
        store.audit("system", "backup.drill", "cloud",
                    {"file": newest.name, "counts": counts,
                     "latest_day": str(latest), "rto_s": took})
        return {"ok": True, "file": newest.name, "counts": counts,
                "latest_day": str(latest), "rto_s": took}
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


def demo_heartbeat() -> dict[str, int]:
    """演示模式：种子里的边没有真实上报，2 小时后会被判掉线。

    这里让它们按小时补一条空的收件记录，只更新 last_seen 与 reports，
    不改日聚合、不写告警。真上报覆盖同一张表，所以接真机后关掉 DEMO 即可。
    """
    if not config.DEMO:
        return {"beats": 0}
    now = store.now_ms()
    cutoff = now - 90 * 60_000
    beats = 0
    with store.connect() as conn:
        stale = [r["edge_id"] for r in conn.execute(
            "SELECT edge_id FROM edges WHERE last_seen < %s", (cutoff,))]
        for edge_id in stale:
            conn.execute("UPDATE edges SET last_seen=%s WHERE edge_id=%s", (now, edge_id))
            conn.execute("UPDATE devices SET last_seen=%s WHERE edge_id=%s", (now, edge_id))
            conn.execute(
                "INSERT INTO reports(edge_id, report_id, received_at, raw_bytes)"
                " VALUES(%s,%s,%s,0) ON CONFLICT DO NOTHING",
                (edge_id, f"demo-beat-{now // 3_600_000}", store._ms(now)))
            beats += 1
    return {"beats": beats}


def prune(retention_days: int | None = None) -> dict[str, int]:
    days = retention_days or config.RETENTION_DAYS
    text_days = config.TEXT_RETENTION_DAYS
    today = dt.date.today()
    cutoff_day = (today - dt.timedelta(days=days)).isoformat()
    text_day = (today - dt.timedelta(days=text_days)).isoformat()
    cutoff_ms = store.now_ms() - days * 86_400_000
    text_ms = store.now_ms() - text_days * 86_400_000
    cutoff = dt.datetime.fromtimestamp(cutoff_ms / 1000.0, tz=dt.timezone.utc)
    text_cutoff = dt.datetime.fromtimestamp(text_ms / 1000.0, tz=dt.timezone.utc)
    with store.connect() as conn:
        daily = conn.execute("DELETE FROM telemetry_daily WHERE day < %s", (cutoff_day,)).rowcount
        adv = conn.execute("DELETE FROM advice WHERE created_at < %s", (text_cutoff,)).rowcount
        words = conn.execute("DELETE FROM word_freq WHERE day < %s", (text_day,)).rowcount
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
    "run_restore_drill": run_restore_drill,
    "prune": prune,
    "maybe_daily_report": maybe_daily_report,
}
