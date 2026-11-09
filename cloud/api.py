"""云端 API。公网上只应暴露 /ingest/edge-report 与 /health，其余仅内网。"""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import ValidationError

from . import ai, auth, config, models, scheduler, store

router = APIRouter(prefix="/api/v1")


def admin_guard(request: Request,
                authorization: str | None = Header(default=None)) -> None:
    """三种放行：bearer 令牌、登录会话、或本机演示（两个都没配）。"""
    if config.ADMIN_TOKEN and authorization == f"Bearer {config.ADMIN_TOKEN}":
        return
    if auth.required():
        if auth.check_session(request.cookies.get(auth.COOKIE)):
            return
        raise HTTPException(401, "需要登录")
    if config.ADMIN_TOKEN:
        raise HTTPException(401, "需要管理员令牌")


def edge_guard(authorization: str | None = Header(default=None)) -> None:
    if not config.EDGE_TOKEN:
        return
    if authorization != f"Bearer {config.EDGE_TOKEN}":
        raise HTTPException(401, "边证书或令牌无效")


admin = [Depends(admin_guard)]


def _raw_hits(node: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if k in models.RAW_KEYS and p not in ("privacy.raw",):
                hits.append(p)
            hits += _raw_hits(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            hits += _raw_hits(v, f"{path}[{i}]")
    return hits


@router.get("/auth/state")
def auth_state(request: Request) -> dict[str, Any]:
    return auth.state(request.cookies.get(auth.COOKIE))


@router.post("/auth/login")
def auth_login(body: dict[str, Any], response: Response) -> dict[str, Any]:
    result = auth.login(str(body.get("password", "")), str(body.get("code", "")))
    if not result["ok"]:
        raise HTTPException(401, result["error"])
    response.set_cookie(auth.COOKIE, result["token"], httponly=True, samesite="lax",
                        max_age=auth.SESSION_TTL, path="/")
    return {"ok": True}


@router.post("/auth/logout")
def auth_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.post("/ingest/edge-report")
def ingest(body: dict[str, Any],
           authorization: str | None = Header(default=None)) -> dict[str, Any]:
    edge_guard(authorization)

    hits = _raw_hits(body)
    if hits:
        store.bump("raw_rejected")
        store.audit("edge", "ingest.reject", body.get("edge_id", "?"), {"fields": hits[:5]})
        raise HTTPException(422, {"code": "RAW_PAYLOAD_REJECTED",
                                  "message": "云端只接受 L2 聚合，不接受原始字段",
                                  "fields": hits[:5]})
    try:
        msg = models.EdgeReport.model_validate(body)
    except ValidationError as e:
        store.audit("edge", "ingest.invalid", body.get("edge_id", "?"),
                    {"errors": e.errors(include_url=False, include_context=False)[:3]})
        raise HTTPException(422, {"code": "SCHEMA_INVALID",
                                  "errors": e.errors(include_url=False, include_context=False)[:5]})

    result = store.ingest_report(msg.model_dump(by_alias=True))
    if not result["duplicate"]:
        store.bump("suppressed_buckets", msg.privacy.suppressed_buckets)
        store.bump("raw_rejected", msg.privacy.raw_rejected)
        store.bump("rows_ingested", result["rows"])
        store.audit("edge", "ingest.accept", msg.edge_id,
                    {"report_id": msg.report_id, "rows": result["rows"]})
    return {"accepted": True, "duplicate": result["duplicate"],
            "report_id": msg.report_id, "rows": result["rows"]}


@router.get("/meta", dependencies=admin)
def meta() -> dict[str, Any]:
    """指标字典来自已入库的数据：换了模块，下拉框自动多一项。"""
    return {
        "metrics": [{"key": r["metric"], "label": r["label"], "unit": r["unit"],
                     "module_id": r["module_id"], "devices": r["devices"]}
                    for r in store.metric_labels()],
        "ai_mode": config.AI_MODE, "k_min": config.K_MIN,
        "cloud_name": config.CLOUD_NAME, "cloud_id": config.CLOUD_ID,
    }


# ---------------------------------------------------------------- 总览

@router.get("/overview", dependencies=admin)
def overview() -> dict[str, Any]:
    now = store.now_ms()
    es = store.edges()
    ds = store.devices()
    online_edges = [e for e in es if e["last_seen"] and now - e["last_seen"] < 2 * 3600_000]
    online_devices = [d for d in ds if d["online"]]

    today = dt.date.today()
    week_ago = (today - dt.timedelta(days=6)).isoformat()
    rows = [r for r in store.daily_rows(4000) if r["day"] >= week_ago]
    # 完整率按「设备日」算：一台设备一天有数据才算完整，缺一台也能看见
    device_days = {(r["device_id"], r["day"]) for r in rows}
    complete = len(device_days) / max(1, len(ds) * 7) * 100

    alerts = store.advice(risk="alert", limit=200)
    top: dict[str, dict] = {}
    for a in alerts:
        key = a["text"]
        top.setdefault(key, {"text": key, "count": 0, "edge": a["edge_id"],
                             "device": a["device_id"], "risk": a["risk"]})
        top[key]["count"] += 1
    alerts_top = sorted(top.values(), key=lambda x: -x["count"])[:5]

    latest = store.reports(1)
    counter = store.counters()
    return {
        "cloud_id": config.CLOUD_ID, "cloud_name": config.CLOUD_NAME,
        "generated_at": now,
        "edges_total": len(es), "edges_online": len(online_edges),
        "devices_total": len(ds), "devices_online": len(online_devices),
        "completeness_7d": round(complete, 1),
        "rows_7d": len(rows),
        "alerts_total": len(alerts),
        "alerts_top": alerts_top,
        "k_min_seen": min([r["k"] for r in rows] or [0]),
        "suppressed_buckets": counter.get("suppressed_buckets", 0),
        "raw_rejected": counter.get("raw_rejected", 0),
        "latest_ai": latest[0] if latest else None,
        "ai_mode": config.AI_MODE,
        "edges": es,
    }


@router.get("/edges", dependencies=admin)
def edges() -> list[dict[str, Any]]:
    today = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    rows = [r for r in store.daily_rows(4000) if r["day"] >= today]
    out = []
    for e in store.edges():
        erows = [r for r in rows if r["edge_id"] == e["edge_id"]]
        metrics = sorted({r["metric"] for r in erows})
        out.append({**e,
                    "days": len({r["day"] for r in erows}),
                    "metrics": metrics,
                    "k_min": min([r["k"] for r in erows] or [0]),
                    "devices_list": [d for d in store.devices(e["edge_id"])]})
    return out


@router.get("/devices", dependencies=admin)
def devices(edge: str | None = None) -> list[dict[str, Any]]:
    today = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    rows = [r for r in store.daily_rows(4000) if r["day"] >= today]
    out = []
    for d in store.devices(edge):
        drows = [r for r in rows if r["device_id"] == d["device_id"]]
        out.append({**d,
                    "days": len({r["day"] for r in drows}),
                    "metrics": sorted({r["metric"] for r in drows}),
                    "k_min": min([r["k"] for r in drows] or [0])})
    return out


@router.get("/series", dependencies=admin)
def series(metric: str, edge: str | None = None, device: str | None = None) -> dict[str, Any]:
    pts = store.series(edge, device, metric)
    return {"metric": metric, "points": pts,
            "k_min": min([p["k"] for p in pts] or [0])}


@router.get("/heatmap", dependencies=admin)
def heatmap(edge: str, device: str, metric: str) -> dict[str, Any]:
    return {"metric": metric, "cells": store.heatmap(edge, device, metric)}


@router.get("/wordcloud", dependencies=admin)
def wordcloud(days: int = Query(default=7, ge=1, le=30), scope: str | None = None) -> dict[str, Any]:
    return {"words": store.wordcloud(scope, days, dt.date.today().isoformat())}


@router.get("/advice", dependencies=admin)
def advice(edge: str | None = None, risk: str | None = None,
           limit: int = Query(default=40, ge=1, le=200)) -> list[dict[str, Any]]:
    return store.advice(edge, risk, limit)


@router.get("/daily", dependencies=admin)
def daily(edge: str | None = None, limit: int = Query(default=40, ge=1, le=500)) -> list[dict[str, Any]]:
    return store.daily_rows(limit, edge)


@router.get("/compare", dependencies=admin)
def compare(metric: str) -> dict[str, Any]:
    """多边同指标对比：原始值 + 以首日为 100 的归一化指数。"""
    es = store.edges()
    out = []
    for e in es:
        pts = store.series(e["edge_id"], None, metric)
        if not pts:
            continue
        base = pts[0]["mean"] or 1
        out.append({
            "edge_id": e["edge_id"], "name": e["name"],
            "points": [{"day": p["day"], "value": p["mean"],
                        "index": round(p["mean"] / base * 100, 1)} for p in pts],
            "k_min": min(p["k"] for p in pts),
        })
    days = sorted({p["day"] for s in out for p in s["points"]})
    return {"metric": metric, "edges": out, "days": days}


# ---------------------------------------------------------------- AI 报告

@router.get("/ai/reports", dependencies=admin)
def ai_reports(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    return store.reports(limit)


@router.get("/ai/reports/{report_id}", dependencies=admin)
def ai_report(report_id: int) -> dict[str, Any]:
    r = store.report(report_id)
    if not r:
        raise HTTPException(404, "报告不存在")
    return r


@router.post("/ai/reports", dependencies=admin)
def ai_generate(body: dict[str, Any]) -> dict[str, Any]:
    scope = body.get("scope", "fleet")
    if scope not in {"fleet", "edge", "device"}:
        raise HTTPException(400, "scope 只能是 fleet / edge / device")
    scope_id = body.get("scope_id")
    if scope != "fleet" and not scope_id:
        raise HTTPException(400, "edge / device 必须给 scope_id")
    mode = body.get("mode", config.AI_MODE)
    task = body.get("task", "weekly_narrative")
    if task not in ai.TASKS:
        raise HTTPException(400, f"task 只能是 {'/'.join(ai.TASKS)}")
    result = ai.generate(scope, scope_id, force_rules=(mode == "rules"), task=task)
    # 生成后写一条导出记录，供隐私审计页展示
    store.audit("admin", "export.report", f"report:{result['id']}",
                {"model": result["model"], "scope": scope})
    return result


# ---------------------------------------------------------------- 隐私审计

@router.get("/privacy/audit", dependencies=admin)
def privacy_audit() -> dict[str, Any]:
    today = dt.date.today()
    rows = store.daily_rows(4000)
    ks = [r["k"] for r in rows]
    dist: dict[str, int] = {}
    for k in ks:
        b = "5" if k <= 5 else "6-7" if k <= 7 else "8-9" if k <= 9 else "10+"
        dist[b] = dist.get(b, 0) + 1
    c = store.counters()
    return {
        "k_min": config.K_MIN,
        "rows": len(rows),
        "k_distribution": dist,
        "k_min_seen": min(ks) if ks else 0,
        "suppressed_buckets": c.get("suppressed_buckets", 0),
        "raw_rejected": c.get("raw_rejected", 0),
        "rows_ingested": c.get("rows_ingested", 0),
        "report_count": len(store.reports(500)),
        "exports": [a for a in store.audit_log(50) if a["action"].startswith("export")],
        "audit": store.audit_log(10),
        "retention_days": config.RETENTION_DAYS,
        "fields_kept": ["mean", "slope", "z", "n_subjects", "n_samples", "k", "day"],
        "fields_rejected": list(sorted(models.RAW_KEYS)),
    }


@router.get("/health")
def health() -> dict[str, Any]:
    try:
        with store.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        db = "ok"
    except Exception:  # noqa: BLE001
        db = "fail"
    return {
        "ok": db == "ok", "cloud_id": config.CLOUD_ID, "db": db,
        "edge_token": "set" if config.EDGE_TOKEN else "open",
        "admin_token": "set" if config.ADMIN_TOKEN else "open",
        "ai_mode": config.AI_MODE,
        "ollama": config.OLLAMA_URL if config.AI_MODE == "local" else None,
        "edges": len(store.edges()), "devices": len(store.devices()),
        "ingested_rows": store.counters().get("rows_ingested", 0),
        "auth_required": auth.required(),
        "api_tokens_used": store.counters().get("api_tokens", 0),
        "api_budget": config.API_BUDGET_TOKENS,
        "scheduler": {"ticks": scheduler.scheduler.ticks, "last": scheduler.scheduler.last},
    }
