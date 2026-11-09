"""云存储层：主库 Postgres 16，DDL 与架构文档 §6.2 一致。

只存日聚合、建议文本与 AI 报告，不存逐条原始记录。
库里的时间列按文档用 DATE / TIMESTAMPTZ，但对外的 JSON 仍是毫秒整数，
前端不用为换库改一行。
"""
from __future__ import annotations

import datetime as dt
import json
import time
from decimal import Decimal
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS edges (
  edge_id   TEXT PRIMARY KEY,
  name      TEXT,
  cert_fp   TEXT,
  last_seen BIGINT,      -- 毫秒时间戳，INTEGER 装不下（文档写的是 INTEGER，这里是必要的修正）
  version   TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  device_id    TEXT PRIMARY KEY,
  edge_id      TEXT NOT NULL REFERENCES edges(edge_id),
  scene        TEXT,
  modules_json TEXT,
  last_seen    BIGINT
);

CREATE TABLE IF NOT EXISTS telemetry_daily (
  edge_id     TEXT,
  device_id   TEXT,
  day         DATE,
  metric      TEXT,
  n_subjects  INTEGER,
  n_samples   INTEGER,
  mean        DOUBLE PRECISION,
  slope       DOUBLE PRECISION,
  z           DOUBLE PRECISION,
  k           INTEGER,
  module_id   INTEGER DEFAULT 0,
  label       TEXT,
  unit        TEXT,
  table_name  TEXT,
  PRIMARY KEY (edge_id, device_id, day, metric)
);
CREATE INDEX IF NOT EXISTS ix_daily_metric ON telemetry_daily(metric, day);
CREATE INDEX IF NOT EXISTS ix_daily_edge ON telemetry_daily(edge_id, day);

CREATE TABLE IF NOT EXISTS advice (
  id         BIGSERIAL PRIMARY KEY,
  edge_id    TEXT,
  device_id  TEXT,
  text       TEXT,
  risk       TEXT,
  confidence DOUBLE PRECISION,
  source     TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(edge_id, device_id, created_at, text)
);

CREATE TABLE IF NOT EXISTS ai_reports (
  id           BIGSERIAL PRIMARY KEY,
  scope        TEXT,
  scope_id     TEXT,
  period_start DATE,
  period_end   DATE,
  model        TEXT,
  prompt_hash  TEXT,
  content      TEXT,
  tokens_in    INTEGER DEFAULT 0,
  tokens_out   INTEGER DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS word_freq (
  day   DATE,
  scope TEXT,
  word  TEXT,
  count INTEGER,
  PRIMARY KEY (day, scope, word)
);

CREATE TABLE IF NOT EXISTS audit (
  id         BIGSERIAL PRIMARY KEY,
  actor      TEXT,
  action     TEXT,
  target     TEXT,
  detail     JSONB,
  created_at TIMESTAMPTZ NOT NULL
);

-- 文档之外的两张辅助表：幂等收件箱与计数器
CREATE TABLE IF NOT EXISTS reports (
  edge_id     TEXT NOT NULL,
  report_id   TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  raw_bytes   INTEGER NOT NULL,
  PRIMARY KEY (edge_id, report_id)
);

CREATE TABLE IF NOT EXISTS counters (
  key   TEXT PRIMARY KEY,
  value BIGINT NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def _ms(when: Any) -> Any:
    """ms 整数 → TIMESTAMPTZ 参数。"""
    return dt.datetime.fromtimestamp(when / 1000.0, tz=dt.timezone.utc)


def _plain(value: Any) -> Any:
    """把库里的 date/datetime/Decimal 还原成 JSON 友好的类型。"""
    if isinstance(value, dt.datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (dt.timedelta,)):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: _plain(v) for k, v in row.items()}


@contextmanager
def connect():
    """一次事务。调用方不 commit，退出时统一提交，异常回滚。"""
    with psycopg.connect(config.PG_DSN, row_factory=dict_row) as conn:
        yield conn


def init_db() -> None:
    # 串行化建表：多个进程同时起时不打架
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(20261109)")
        for statement in SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        # 老库修正：毫秒时间戳必须能放进列里
        for table in ("edges", "devices"):
            row = conn.execute(
                "SELECT data_type FROM information_schema.columns"
                " WHERE table_name=%s AND column_name='last_seen'", (table,)).fetchone()
            if row and row["data_type"] == "integer":
                conn.execute(f"ALTER TABLE {table} ALTER COLUMN last_seen TYPE BIGINT")


def bump(key: str, n: int = 1) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO counters(key, value) VALUES(%s,%s) "
            "ON CONFLICT(key) DO UPDATE SET value = counters.value + EXCLUDED.value", (key, n))


def counters() -> dict[str, int]:
    with connect() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT * FROM counters")}


def audit(actor: str, action: str, target: str, detail: dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit(actor, action, target, detail, created_at) VALUES(%s,%s,%s,%s,%s)",
            (actor, action, target, json.dumps(detail or {}, ensure_ascii=False, default=str),
             _ms(now_ms())))


def ingest_report(payload: dict[str, Any]) -> dict[str, Any]:
    """幂等写入一份 edge-report。返回 {duplicate, rows}。"""
    edge_id = payload["edge_id"]
    report_id = payload["report_id"]
    now = now_ms()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO reports(edge_id, report_id, received_at, raw_bytes) VALUES(%s,%s,%s,%s)"
            " ON CONFLICT DO NOTHING",
            (edge_id, report_id, _ms(now), len(json.dumps(payload))))
        if cur.rowcount == 0:
            return {"duplicate": True, "rows": 0}
        conn.execute(
            """INSERT INTO edges(edge_id, name, last_seen, version) VALUES(%s,%s,%s,%s)
               ON CONFLICT(edge_id) DO UPDATE SET
                 name=EXCLUDED.name, last_seen=EXCLUDED.last_seen""",
            (edge_id, payload.get("edge_name") or edge_id, now, "1.0.0"))
        for d in payload["devices"]:
            conn.execute(
                """INSERT INTO devices(device_id, edge_id, scene, modules_json, last_seen)
                   VALUES(%s,%s,%s,%s,%s)
                   ON CONFLICT(device_id) DO UPDATE SET
                     edge_id=EXCLUDED.edge_id, scene=EXCLUDED.scene,
                     modules_json=EXCLUDED.modules_json, last_seen=EXCLUDED.last_seen""",
                (d["device_id"], edge_id, d["scene"], json.dumps(d["modules"]),
                 d.get("last_seen") or now))
        for row in payload["telemetry_daily"]:
            conn.execute(
                """INSERT INTO telemetry_daily
                   (edge_id, device_id, day, metric, n_subjects, n_samples, mean, slope, z, k,
                    module_id, label, unit, table_name)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(edge_id, device_id, day, metric) DO UPDATE SET
                     n_subjects=EXCLUDED.n_subjects, n_samples=EXCLUDED.n_samples,
                     mean=EXCLUDED.mean, slope=EXCLUDED.slope, z=EXCLUDED.z, k=EXCLUDED.k,
                     module_id=EXCLUDED.module_id,
                     label=COALESCE(NULLIF(EXCLUDED.label,''), telemetry_daily.label),
                     unit=COALESCE(NULLIF(EXCLUDED.unit,''), telemetry_daily.unit),
                     table_name=COALESCE(NULLIF(EXCLUDED.table_name,''),
                                        telemetry_daily.table_name)""",
                (edge_id, row["device_id"], row["day"], row["metric"], row["n_subjects"],
                 row["n_samples"], row["mean"], row["slope"], row["z"], row["k"],
                 row.get("module_id", 0), row.get("label", ""), row.get("unit", ""),
                 row.get("table", "samples")))
        for a in payload["advice"]:
            conn.execute(
                """INSERT INTO advice
                   (edge_id, device_id, text, risk, confidence, source, created_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (edge_id, a["device_id"], a["text"], a["risk"], a["confidence"],
                 a["source"], _ms(a["created_at"])))
        for w in payload["word_freq"]:
            conn.execute(
                """INSERT INTO word_freq(day, scope, word, count) VALUES(%s,%s,%s,%s)
                   ON CONFLICT(day, scope, word) DO UPDATE SET count=EXCLUDED.count""",
                (w["day"], edge_id, w["word"], w["count"]))
        return {"duplicate": False, "rows": len(payload["telemetry_daily"])}


# ---------------------------------------------------------------- 查询

def edges() -> list[dict[str, Any]]:
    cutoff = now_ms() - 2 * 3600_000
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.*,
                      (SELECT COUNT(*) FROM devices d WHERE d.edge_id=e.edge_id) AS devices,
                      (SELECT COUNT(*) FROM devices d
                        WHERE d.edge_id=e.edge_id AND d.last_seen>=%s) AS online_devices
               FROM edges e ORDER BY e.edge_id""", (cutoff,)).fetchall()
    return [_row(r) for r in rows]


def devices(edge_id: str | None = None) -> list[dict[str, Any]]:
    """online 由 last_seen 现算，库里不再存这一列。"""
    sql = "SELECT * FROM devices"
    args: tuple = ()
    if edge_id:
        sql += " WHERE edge_id=%s"
        args = (edge_id,)
    sql += " ORDER BY edge_id, device_id"
    cutoff = now_ms() - 2 * 3600_000
    with connect() as conn:
        rows = [_row(r) for r in conn.execute(sql, args)]
    for r in rows:
        r["online"] = bool(r["last_seen"]) and r["last_seen"] >= cutoff
    return rows


def series(edge_id: str | None, device_id: str | None, metric: str) -> list[dict[str, Any]]:
    sql = "SELECT day, device_id, edge_id, mean, z, k FROM telemetry_daily WHERE metric=%s"
    args: list[Any] = [metric]
    if edge_id:
        sql += " AND edge_id=%s"; args.append(edge_id)
    if device_id:
        sql += " AND device_id=%s"; args.append(device_id)
    sql += " ORDER BY day"
    with connect() as conn:
        rows = [_row(r) for r in conn.execute(sql, args)]
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
            "SELECT day, mean, z FROM telemetry_daily"
            " WHERE edge_id=%s AND device_id=%s AND metric=%s",
            (edge_id, device_id, metric)).fetchall()
    return [{"day": r["day"].isoformat() if isinstance(r["day"], dt.date) else r["day"],
             "mean": r["mean"], "z": r["z"]} for r in rows]


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
    return [_row(r) for r in rows]


def daily_rows(limit: int = 200, edge_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM telemetry_daily"
    args: list[Any] = []
    if edge_id:
        sql += " WHERE edge_id=%s"
        args.append(edge_id)
    sql += " ORDER BY day DESC, device_id, metric LIMIT %s"
    args.append(limit)
    with connect() as conn:
        return [_row(r) for r in conn.execute(sql, args)]


def advice(edge_id: str | None = None, risk: str | None = None,
           limit: int = 50) -> list[dict[str, Any]]:
    sql = "SELECT * FROM advice WHERE TRUE"
    args: list[Any] = []
    if edge_id:
        sql += " AND edge_id=%s"; args.append(edge_id)
    if risk:
        sql += " AND risk=%s"; args.append(risk)
    sql += " ORDER BY created_at DESC LIMIT %s"
    args.append(limit)
    with connect() as conn:
        return [_row(r) for r in conn.execute(sql, args)]


def wordcloud(scope: str | None, days: int, today: str) -> list[dict[str, Any]]:
    sql = "SELECT word, SUM(count) AS count FROM word_freq WHERE day>=%s"
    args: list[Any] = [_shift(today, -(days - 1))]
    if scope:
        sql += " AND scope=%s"; args.append(scope)
    sql += " GROUP BY word ORDER BY count DESC LIMIT 40"
    with connect() as conn:
        return [_row(r) for r in conn.execute(sql, args)]


def _shift(day: str, delta: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=delta)).isoformat()


def reports(limit: int = 30) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, scope, scope_id, period_start, period_end, model, prompt_hash,"
            " tokens_in, tokens_out, created_at,"
            " COALESCE(content::jsonb->>'task','weekly_narrative') AS task"
            " FROM ai_reports ORDER BY created_at DESC LIMIT %s",
            (limit,)).fetchall()
    return [_row(r) for r in rows]


def report(report_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM ai_reports WHERE id=%s", (report_id,)).fetchone()
    if not r:
        return None
    d = _row(r)
    d["content"] = json.loads(d["content"])
    return d


def find_report(scope: str, scope_id: str | None, period_end: str,
                prompt_hash: str, since_ms: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT id, model FROM ai_reports
               WHERE scope=%s AND COALESCE(scope_id,'')=COALESCE(%s,'')
                 AND period_end=%s AND prompt_hash=%s AND created_at>=%s
               ORDER BY id DESC LIMIT 1""",
            (scope, scope_id, period_end, prompt_hash, _ms(since_ms))).fetchone()
    return _row(row)


def save_report(scope: str, scope_id: str | None, period_start: str, period_end: str,
                model: str, prompt_hash: str, content: dict[str, Any],
                tokens_in: int, tokens_out: int) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO ai_reports(scope, scope_id, period_start, period_end, model,
               prompt_hash, content, tokens_in, tokens_out, created_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (scope, scope_id, period_start, period_end, model, prompt_hash,
             json.dumps(content, ensure_ascii=False), tokens_in, tokens_out, _ms(now_ms())))
        return int(cur.fetchone()["id"])


def audit_log(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM audit ORDER BY created_at DESC LIMIT %s", (limit,))]
