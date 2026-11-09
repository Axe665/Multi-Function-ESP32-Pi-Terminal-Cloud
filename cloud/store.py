"""云存储层：只存日聚合、建议文本与 AI 报告，不存逐条原始记录。"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS edges (
  edge_id    TEXT PRIMARY KEY,
  name       TEXT,
  cert_fp    TEXT,
  last_seen  INTEGER,
  version    TEXT,
  scene_note TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  device_id   TEXT PRIMARY KEY,
  edge_id     TEXT NOT NULL REFERENCES edges(edge_id),
  scene       TEXT,
  modules_json TEXT,
  last_seen   INTEGER,
  online      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS telemetry_daily (
  edge_id   TEXT NOT NULL,
  device_id TEXT NOT NULL,
  day       TEXT NOT NULL,
  metric    TEXT NOT NULL,
  n_subjects INTEGER NOT NULL,
  n_samples INTEGER NOT NULL,
  mean      REAL NOT NULL,
  slope     REAL NOT NULL,
  z         REAL NOT NULL,
  k         INTEGER NOT NULL,
  module_id INTEGER DEFAULT 0,
  label     TEXT,
  unit      TEXT,
  table_name TEXT,
  PRIMARY KEY (edge_id, device_id, day, metric)
);
CREATE INDEX IF NOT EXISTS ix_daily_metric ON telemetry_daily(metric, day);

CREATE TABLE IF NOT EXISTS advice (
  id         INTEGER PRIMARY KEY,
  edge_id    TEXT NOT NULL,
  device_id  TEXT NOT NULL,
  text       TEXT NOT NULL,
  risk       TEXT NOT NULL,
  confidence REAL NOT NULL,
  source     TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(edge_id, device_id, created_at, text)
);

CREATE TABLE IF NOT EXISTS ai_reports (
  id          INTEGER PRIMARY KEY,
  scope       TEXT NOT NULL,
  scope_id    TEXT,
  period_start TEXT NOT NULL,
  period_end   TEXT NOT NULL,
  model       TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  content     TEXT NOT NULL,
  tokens_in   INTEGER NOT NULL DEFAULT 0,
  tokens_out  INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS word_freq (
  day   TEXT NOT NULL,
  scope TEXT NOT NULL,
  word  TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (day, scope, word)
);

CREATE TABLE IF NOT EXISTS reports (
  edge_id   TEXT NOT NULL,
  report_id TEXT NOT NULL,
  received_at INTEGER NOT NULL,
  raw_bytes INTEGER NOT NULL,
  PRIMARY KEY (edge_id, report_id)
);

CREATE TABLE IF NOT EXISTS audit (
  id        INTEGER PRIMARY KEY,
  actor     TEXT,
  action    TEXT,
  target    TEXT,
  detail    TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
  key   TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


@contextmanager
def connect():
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(telemetry_daily)")}
        for name, decl in (("module_id", "INTEGER DEFAULT 0"), ("label", "TEXT"),
                           ("unit", "TEXT"), ("table_name", "TEXT")):
            if name not in cols:
                conn.execute(f"ALTER TABLE telemetry_daily ADD COLUMN {name} {decl}")


def bump(key: str, n: int = 1) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO counters(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value", (key, n))


def counters() -> dict[str, int]:
    with connect() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT * FROM counters")}


def audit(actor: str, action: str, target: str, detail: dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit(actor, action, target, detail, created_at) VALUES(?,?,?,?,?)",
            (actor, action, target, json.dumps(detail or {}, ensure_ascii=False, default=str), now_ms()))


def ingest_report(payload: dict[str, Any]) -> dict[str, Any]:
    """幂等写入一份 edge-report。返回 {duplicate, rows}。"""
    edge_id = payload["edge_id"]
    report_id = payload["report_id"]
    now = now_ms()
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO reports(edge_id, report_id, received_at, raw_bytes) VALUES(?,?,?,?)",
            (edge_id, report_id, now, len(json.dumps(payload))))
        if cur.rowcount == 0:
            return {"duplicate": True, "rows": 0}
        conn.execute(
            """INSERT INTO edges(edge_id, name, last_seen, version) VALUES(?,?,?,?)
               ON CONFLICT(edge_id) DO UPDATE SET
                 name=excluded.name, last_seen=excluded.last_seen""",
            (edge_id, payload.get("edge_name") or edge_id, now, "1.0.0"))
        for d in payload["devices"]:
            conn.execute(
                """INSERT INTO devices(device_id, edge_id, scene, modules_json, last_seen, online)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(device_id) DO UPDATE SET
                     edge_id=excluded.edge_id, scene=excluded.scene,
                     modules_json=excluded.modules_json, last_seen=excluded.last_seen,
                     online=excluded.online""",
                (d["device_id"], edge_id, d["scene"], json.dumps(d["modules"]),
                 d.get("last_seen") or now, 1 if d.get("online") else 0))
        for row in payload["telemetry_daily"]:
            conn.execute(
                """INSERT INTO telemetry_daily
                   (edge_id, device_id, day, metric, n_subjects, n_samples, mean, slope, z, k,
                    module_id, label, unit, table_name)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(edge_id, device_id, day, metric) DO UPDATE SET
                     n_subjects=excluded.n_subjects, n_samples=excluded.n_samples,
                     mean=excluded.mean, slope=excluded.slope, z=excluded.z, k=excluded.k,
                     module_id=excluded.module_id,
                     label=COALESCE(NULLIF(excluded.label,''), telemetry_daily.label),
                     unit=COALESCE(NULLIF(excluded.unit,''), telemetry_daily.unit),
                     table_name=COALESCE(NULLIF(excluded.table_name,''), telemetry_daily.table_name)""",
                (edge_id, row["device_id"], row["day"], row["metric"], row["n_subjects"],
                 row["n_samples"], row["mean"], row["slope"], row["z"], row["k"],
                 row.get("module_id", 0), row.get("label", ""), row.get("unit", ""),
                 row.get("table", "samples")))
        for a in payload["advice"]:
            conn.execute(
                """INSERT OR IGNORE INTO advice
                   (edge_id, device_id, text, risk, confidence, source, created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (edge_id, a["device_id"], a["text"], a["risk"], a["confidence"],
                 a["source"], a["created_at"]))
        for w in payload["word_freq"]:
            conn.execute(
                """INSERT INTO word_freq(day, scope, word, count) VALUES(?,?,?,?)
                   ON CONFLICT(day, scope, word) DO UPDATE SET count=excluded.count""",
                (w["day"], edge_id, w["word"], w["count"]))
        return {"duplicate": False, "rows": len(payload["telemetry_daily"])}


# ---------------------------------------------------------------- 查询

def edges() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM edges ORDER BY edge_id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["devices"] = conn.execute(
                "SELECT COUNT(*) FROM devices WHERE edge_id=?", (r["edge_id"],)).fetchone()[0]
            d["online_devices"] = conn.execute(
                "SELECT COUNT(*) FROM devices WHERE edge_id=? AND last_seen>=?",
                (r["edge_id"], now_ms() - 2 * 3600_000)).fetchone()[0]
            out.append(d)
    return out


def devices(edge_id: str | None = None) -> list[dict[str, Any]]:
    """online 由 last_seen 现算，不用存的那一列 —— 存的值会一直停在最后一次上报。"""
    sql = "SELECT * FROM devices"
    args: tuple = ()
    if edge_id:
        sql += " WHERE edge_id=?"
        args = (edge_id,)
    sql += " ORDER BY edge_id, device_id"
    cutoff = now_ms() - 2 * 3600_000
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, args)]
    for r in rows:
        r["online"] = bool(r["last_seen"]) and r["last_seen"] >= cutoff
    return rows


def series(edge_id: str | None, device_id: str | None, metric: str) -> list[dict[str, Any]]:
    sql = "SELECT day, device_id, edge_id, mean, z, k FROM telemetry_daily WHERE metric=?"
    args: list[Any] = [metric]
    if edge_id:
        sql += " AND edge_id=?"; args.append(edge_id)
    if device_id:
        sql += " AND device_id=?"; args.append(device_id)
    sql += " ORDER BY day"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    # 同一天多设备时取均值，用于跨边对比
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(r["day"], []).append(r)
    return [{"day": d, "mean": sum(x["mean"] for x in xs) / len(xs),
             "z": sum(x["z"] for x in xs) / len(xs),
             "k": min(x["k"] for x in xs), "n": len(xs)}
            for d, xs in sorted(buckets.items())]


def heatmap(edge_id: str, device_id: str, metric: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT day, mean, z FROM telemetry_daily WHERE edge_id=? AND device_id=? AND metric=?",
            (edge_id, device_id, metric)).fetchall()
    return [{"day": r["day"], "mean": r["mean"], "z": r["z"]} for r in rows]


def metric_labels() -> list[dict[str, Any]]:
    """云收到过哪些指标 —— 前端下拉框与 AI 的措辞都从这里来。"""
    with connect() as conn:
        rows = conn.execute(
            """SELECT metric,
                      MAX(COALESCE(NULLIF(label,''), metric)) AS label,
                      MAX(COALESCE(unit,'')) AS unit,
                      MIN(COALESCE(module_id,0)) AS module_id,
                      COUNT(DISTINCT device_id) AS devices,
                      COUNT(*) AS rows
               FROM telemetry_daily GROUP BY metric ORDER BY metric""").fetchall()
    return [dict(r) for r in rows]


def daily_rows(limit: int = 200, edge_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM telemetry_daily"
    args: list[Any] = []
    if edge_id:
        sql += " WHERE edge_id=?"
        args.append(edge_id)
    sql += " ORDER BY day DESC, device_id, metric LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def advice(edge_id: str | None = None, risk: str | None = None,
           limit: int = 50) -> list[dict[str, Any]]:
    sql = "SELECT * FROM advice WHERE 1=1"
    args: list[Any] = []
    if edge_id:
        sql += " AND edge_id=?"; args.append(edge_id)
    if risk:
        sql += " AND risk=?"; args.append(risk)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def wordcloud(scope: str | None, days: int, today: str) -> list[dict[str, Any]]:
    sql = "SELECT word, SUM(count) AS count FROM word_freq WHERE day>=?"
    args: list[Any] = [_shift(today, -(days - 1))]
    if scope:
        sql += " AND scope=?"; args.append(scope)
    sql += " GROUP BY word ORDER BY count DESC LIMIT 40"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def _shift(day: str, delta: int) -> str:
    import datetime as dt
    return (dt.date.fromisoformat(day) + dt.timedelta(days=delta)).isoformat()


def reports(limit: int = 30) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, scope, scope_id, period_start, period_end, model, prompt_hash,"
            " tokens_in, tokens_out, created_at,"
            " COALESCE(json_extract(content,'$.task'),'weekly_narrative') AS task"
            " FROM ai_reports ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def report(report_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM ai_reports WHERE id=?", (report_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["content"] = json.loads(d["content"])
    return d


def find_report(scope: str, scope_id: str | None, period_end: str,
                prompt_hash: str, since_ms: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT id, model FROM ai_reports
               WHERE scope=? AND IFNULL(scope_id,'')=IFNULL(?,'')
                 AND period_end=? AND prompt_hash=? AND created_at>=?
               ORDER BY id DESC LIMIT 1""",
            (scope, scope_id, period_end, prompt_hash, since_ms)).fetchone()
    return dict(row) if row else None


def save_report(scope: str, scope_id: str | None, period_start: str, period_end: str,
                model: str, prompt_hash: str, content: dict[str, Any],
                tokens_in: int, tokens_out: int) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO ai_reports(scope, scope_id, period_start, period_end, model,
               prompt_hash, content, tokens_in, tokens_out, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (scope, scope_id, period_start, period_end, model, prompt_hash,
             json.dumps(content, ensure_ascii=False), tokens_in, tokens_out, now_ms()))
        return int(cur.lastrowid)


def audit_log(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit ORDER BY created_at DESC LIMIT ?", (limit,))]
