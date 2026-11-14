"""worker 侧的任务函数。

文档 §6.1 的队列里只有三件事：AI 报告生成、词频、跨边聚合。
保留期清理与每日备份也由 worker 进程排期，但它们不进队列语义，
只是同一条时间循环里的动作。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

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
    out = ai.generate(scope, scope_id, task=task, period=rng, mode=mode)
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


def _snapshot_meta(dsn: str) -> dict[str, Any]:
    """备份时点的事实：行数与最新日期。恢复演练拿它当基准，不拿现在的库比。"""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                  for t in ("edges", "devices", "telemetry_daily")}
        latest = conn.execute("SELECT MAX(day) AS d FROM telemetry_daily").fetchone()["d"]
    return {"counts": counts, "latest_day": str(latest) if latest else None,
            "taken_at": store.now_ms()}


def run_backup() -> dict[str, Any]:
    """pg_dump 自定义格式，落 BACKUP_DIR，保留 14 天。

    写完先用 pg_restore --list 验一遍（半截文件不能进保留集），再记一份 meta，
    恢复演练按这份 meta 比。
    """
    config.ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = config.BACKUP_DIR / f"cloud-{stamp}.dump"
    cmd = ["pg_dump", "--format=custom", "--no-owner", "--file", str(target),
           config.PG_DSN]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        store.audit("system", "backup.fail", "cloud", {"error": res.stderr[-300:]})
        store.bump("backup_fail")
        return {"ok": False, "error": res.stderr[-300:]}

    check = subprocess.run(["pg_restore", "--list", str(target)],
                           capture_output=True, text=True, timeout=300)
    if check.returncode != 0:
        store.audit("system", "backup.corrupt", "cloud",
                    {"file": target.name, "error": check.stderr[-300:]})
        store.bump("backup_fail")
        target.unlink(missing_ok=True)
        return {"ok": False, "error": "dump 读不出来，已删除"}

    meta = _snapshot_meta(config.PG_DSN)
    (config.BACKUP_DIR / f"{target.name}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    # 保留 14 天（dump、加密副本、meta 一起清）
    cutoff = time.time() - 14 * 86400
    removed = 0
    for f in config.BACKUP_DIR.glob("cloud-*"):
        if f.suffix not in (".dump", ".gpg", ".json"):
            continue
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    size = target.stat().st_size
    store.audit("system", "backup.done", "cloud",
                {"file": target.name, "bytes": size, "pruned": removed,
                 "rows": meta["counts"], "latest_day": meta["latest_day"]})
    store.bump("backup_ok")
    return {"ok": True, "file": target.name, "bytes": size, "pruned": removed}


def _decrypt(source: Path, target: Path) -> str:
    """把加密副本解到 target；返回空串表示成功。"""
    key_file = config.BACKUP_KEY_FILE
    if not key_file:
        return "没有配置 CLOUD_BACKUP_KEY_FILE，解不开加密副本"
    # worker 以非 root 跑，HOME 不可写；给 gpg 一个临时 homedir
    home = tempfile.mkdtemp(prefix="gpg-")
    try:
        res = subprocess.run(
            ["gpg", "--homedir", home, "--batch", "--yes", "--quiet",
             "--passphrase-file", str(key_file), "--output", str(target), "--decrypt",
             str(source)],
            capture_output=True, text=True, timeout=1800)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return "" if res.returncode == 0 else (res.stderr or res.stdout)[-300:]


def _encrypt(path) -> dict[str, Any]:
    """每周的离线副本加密（age 不在镜像里，用 gpg 对称加密）。

    加密副本落在同一个目录：明文留着等于这份「加密备份」什么都没保护，
    所以验过能解密就把明文删掉。校验不通过时保留明文，下次重试。
    """
    key_file = config.BACKUP_KEY_FILE
    if not key_file:
        # 缺 key 时以前只是返回 False，调用方又把返回值丢了，等于悄悄没加密
        store.audit("system", "backup.encrypt_fail", "cloud",
                    {"file": path.name, "reason": "没有配置 CLOUD_BACKUP_KEY_FILE"})
        store.bump("backup_encrypt_fail")
        return {"ok": False, "error": "没有配置 CLOUD_BACKUP_KEY_FILE"}
    target = path.with_suffix(path.suffix + ".gpg")
    # worker 以非 root 跑，HOME 不可写；给 gpg 一个临时 homedir
    home = tempfile.mkdtemp(prefix="gpg-")
    try:
        res = subprocess.run(
            ["gpg", "--homedir", home, "--batch", "--yes", "--symmetric", "--cipher-algo", "AES256",
             "--passphrase-file", str(key_file), "--output", str(target), str(path)],
            capture_output=True, text=True, timeout=600)
        # 解到 /dev/null：能读出全部包才算真加密成功（--list-packets 不读正文）
        check = subprocess.run(
            ["gpg", "--homedir", home, "--batch", "--yes", "--quiet",
             "--passphrase-file", str(key_file), "--output", os.devnull, "--decrypt",
             str(target)],
            capture_output=True, text=True, timeout=600) if res.returncode == 0 else None
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if res.returncode != 0:
        store.audit("system", "backup.encrypt_fail", "cloud",
                    {"file": path.name, "error": res.stderr[-200:]})
        store.bump("backup_encrypt_fail")
        return {"ok": False, "error": res.stderr[-200:]}
    if check is None or check.returncode != 0:
        target.unlink(missing_ok=True)
        detail = "解密校验失败" if check else "加密失败"
        store.audit("system", "backup.encrypt_fail", "cloud",
                    {"file": path.name, "error": detail})
        store.bump("backup_encrypt_fail")
        return {"ok": False, "error": detail}
    size = target.stat().st_size
    path.unlink(missing_ok=True)
    store.audit("system", "backup.encrypt", "cloud",
                {"file": target.name, "bytes": size, "plain_removed": path.name})
    return {"ok": True, "file": target.name, "bytes": size}


def _drill_fail(reason: str, **extra: Any) -> dict[str, Any]:
    store.audit("system", "backup.drill_fail", "cloud", {"error": reason, **extra})
    store.bump("backup_drill_fail")
    return {"ok": False, "error": reason, **extra}


def run_restore_drill() -> dict[str, Any]:
    """每月一次的恢复演练：新库导入最新备份，与备份时点的 meta 对账，然后删掉。

    备份加密后明文会被删（见 _encrypt），所以候选有 .dump 和 .dump.gpg 两种：
    取最新的那份，.gpg 先解密到临时文件再 pg_restore，用完随临时目录删掉。
    """
    cands = sorted(
        [*config.BACKUP_DIR.glob("cloud-*.dump"), *config.BACKUP_DIR.glob("cloud-*.dump.gpg")],
        key=lambda f: f.stat().st_mtime)
    if not cands:
        return _drill_fail("没有可用的备份文件")
    newest = cands[-1]
    plain = newest.with_suffix("") if newest.suffix == ".gpg" else newest
    meta_file = config.BACKUP_DIR / f"{plain.name}.meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else None
    started = time.time()
    name = f"cloud_drill_{time.strftime('%Y%m%d%H%M%S')}"
    admin = psycopg.conninfo.make_conninfo(config.PG_DSN)
    drill_dsn = psycopg.conninfo.make_conninfo(config.PG_DSN, dbname=name)
    tmpdir: str | None = None
    try:
        source = newest
        if newest.suffix == ".gpg":
            tmpdir = tempfile.mkdtemp(prefix="drill-")
            source = Path(tmpdir) / plain.name
            error = _decrypt(newest, source)
            if error:
                return _drill_fail("加密副本解不开", detail=error)
        with psycopg.connect(admin, autocommit=True, row_factory=dict_row) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        res = subprocess.run(["pg_restore", "--no-owner", "--no-acl", "--dbname", drill_dsn, str(source)],
                             capture_output=True, text=True, timeout=1800)
        # 客户端版本比服务端新时会带一句 SET transaction_timeout，服务端拒绝但数据照常恢复；
        # 真正的判定是 meta 对不对得上，不是 pg_restore 的退出码。
        if res.returncode not in (0, 1):
            return _drill_fail("pg_restore 失败", detail=(res.stderr or res.stdout)[-300:])
        with psycopg.connect(drill_dsn, row_factory=dict_row) as conn:
            counts = {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                      for t in ("edges", "devices", "telemetry_daily")}
            latest = conn.execute("SELECT MAX(day) AS d FROM telemetry_daily").fetchone()["d"]

        if meta:
            short = {t: (meta["counts"][t], counts[t]) for t in counts
                     if counts[t] < meta["counts"][t]}
            if short:
                return _drill_fail("恢复后行数少于备份时点", detail=short)
            if meta.get("latest_day") and str(latest) != meta["latest_day"]:
                return _drill_fail("最新日期与备份时点不一致",
                                   detail={"meta": meta["latest_day"], "restored": str(latest)})
        # 老备份没有 meta：至少要能读出数据
        elif not counts["edges"] or latest is None:
            return _drill_fail("备份里没有数据", detail=counts)

        took = round(time.time() - started, 1)
        store.audit("system", "backup.drill", "cloud",
                    {"file": newest.name, "counts": counts, "meta": meta,
                     "latest_day": str(latest), "rto_s": took})
        store.bump("backup_drill_ok")
        return {"ok": True, "file": newest.name, "counts": counts,
                "latest_day": str(latest), "rto_s": took}
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
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
        # 只补给演示边：以前连真实边一起刷，掉线告警就永远触发不了
        stale = [r["edge_id"] for r in conn.execute(
            "SELECT edge_id FROM edges WHERE is_demo AND last_seen < %s", (cutoff,))]
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
        # AI 报告属于 L3，文档 §8.1 写的是保留 1 年；以前这条表从来不删
        ai_reports = conn.execute(
            "DELETE FROM ai_reports WHERE created_at < %s", (text_cutoff,)).rowcount
    if daily or adv or words or reports or ai_reports:
        store.audit("system", "retention.prune", "cloud",
                    {"daily": daily, "advice": adv, "words": words,
                     # 幂等收件箱也是 L2 元数据，删了多少要和别的表记在同一条明细里
                     "reports": reports, "ai_reports": ai_reports})
    return {"daily": daily, "advice": adv, "words": words, "reports": reports,
            "ai_reports": ai_reports}


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
