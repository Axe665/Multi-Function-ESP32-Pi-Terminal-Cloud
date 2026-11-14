"""云端 API。公网上只应暴露 /ingest/edge-report 与 /health，其余仅内网。"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import ValidationError

from . import ai, auth, config, gpu, mdns, models, queue, store

router = APIRouter(prefix="/api/v1")


def admin_guard(request: Request,
                authorization: str | None = Header(default=None)) -> None:
    """放行只有三种：bearer 令牌、登录会话、或显式打开的演示开关。

    以前「两个都没配」等于放行，默认部署就变成了任何人可读全库、可触发 AI 报告。
    """
    if config.ADMIN_TOKEN and authorization == f"Bearer {config.ADMIN_TOKEN}":
        store.audit("admin", "auth.token", request.url.path, {})
        return
    if auth.required():
        if auth.check_session(request.cookies.get(auth.COOKIE)):
            return
        raise HTTPException(401, "需要登录")
    if config.ADMIN_TOKEN:
        raise HTTPException(401, "需要管理员令牌")
    if config.DEMO_OPEN:
        return
    raise HTTPException(
        403,
        "管理接口未配置鉴权：设置 CLOUD_ADMIN_PASSWORD 与 CLOUD_ADMIN_TOTP；"
        "本机演示可显式设 CLOUD_DEMO_OPEN=1",
    )


def edge_guard(request: Request,
               authorization: str | None = Header(default=None)) -> str:
    """返回客户端证书的 CN（没有就返回空串）。

    8443 上的证书链已经由 Caddy 验过，CN 是可信身份；没有证书时才退回共享令牌。
    """
    subject = request.headers.get("x-client-cn", "")
    match = re.search(r"CN=([^,]+)", subject)
    if match:
        return match.group(1).strip()
    if config.EDGE_TOKEN and authorization == f"Bearer {config.EDGE_TOKEN}":
        return ""
    if config.DEMO_OPEN:
        return ""
    raise HTTPException(
        403,
        "边上报告未鉴权：走 8443 的 mTLS，或配置 CLOUD_EDGE_TOKEN；"
        "本机演示可显式设 CLOUD_DEMO_OPEN=1",
    )


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
def auth_login(body: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
    # 失败计数按来源 IP 记；uvicorn 配了 --forwarded-allow-ips，XFF 是 Caddy 写的
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ip = xff or (request.client.host if request.client else "")
    result = auth.login(str(body.get("password", "")), str(body.get("code", "")), ip)
    if not result["ok"]:
        # 锁定期间回 429，前端能显示还要等多久
        raise HTTPException(429 if result.get("wait") else 401, result["error"])
    # Caddy 在前面终止 TLS，容器里 request.url.scheme 恒为 http；
    # api 端口没发布到宿主、只有 Caddy 连得到，所以 X-Forwarded-Proto 可信。
    secure = (request.url.scheme == "https"
              or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https")
    response.set_cookie(auth.COOKIE, result["token"], httponly=True, samesite="lax",
                        secure=secure, max_age=auth.SESSION_TTL, path="/")
    return {"ok": True}


@router.post("/auth/logout")
def auth_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


def _is_low_k(row: Any) -> bool:
    """k 不是整数就交给模型校验，别在这里替它判断。"""
    if not isinstance(row, dict):
        return False
    try:
        return int(row.get("k")) < config.K_MIN
    except (TypeError, ValueError):
        return False


@router.post("/ingest/edge-report")
def ingest(body: dict[str, Any], cn: str = Depends(edge_guard)) -> dict[str, Any]:
    # 证书 CN 就是边身份：不匹配就不许写别人的边、别人的设备
    if cn and cn != str(body.get("edge_id", "")):
        store.audit("edge", "ingest.cn_mismatch", body.get("edge_id", "?"), {"cn": cn})
        raise HTTPException(403, "证书 CN 与 edge_id 不一致")

    hits = _raw_hits(body)
    if hits:
        store.bump("raw_rejected_local")
        store.audit("edge", "ingest.reject", body.get("edge_id", "?"), {"fields": hits[:5]})
        raise HTTPException(422, {"code": "RAW_PAYLOAD_REJECTED",
                                  "message": "云端只接受 L2 聚合，不接受原始字段",
                                  "fields": hits[:5]})
    # k 门槛与边一致按行丢：整份拒收会让一条低 k 桶把同批合格行一起打回
    rows = body.get("telemetry_daily")
    if isinstance(rows, list):
        kept = [r for r in rows if not _is_low_k(r)]
        dropped = len(rows) - len(kept)
        if dropped:
            body = {**body, "telemetry_daily": kept}
            store.bump("suppressed_buckets", dropped)
            store.audit("edge", "ingest.suppressed", body.get("edge_id", "?"),
                        {"rows": dropped, "k_min": config.K_MIN})
    try:
        msg = models.EdgeReport.model_validate(body)
    except ValidationError as e:
        store.audit("edge", "ingest.invalid", body.get("edge_id", "?"),
                    {"errors": e.errors(include_url=False, include_context=False)[:3]})
        raise HTTPException(422, {"code": "SCHEMA_INVALID",
                                  "errors": e.errors(include_url=False, include_context=False)[:5]})

    result = store.ingest_report(msg.model_dump(by_alias=True))
    if not result["duplicate"]:
        # 这两个数字是边自报的，单独记；审计页要分开展示，不能拿来当云侧的拦截证据
        store.bump("reported_suppressed", msg.privacy.suppressed_buckets)
        store.bump("reported_raw_rejected", msg.privacy.raw_rejected)
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
    off_edges = [e["edge_id"] for e in es if not e.get("group_analysis", True)]
    visible_devices = [d for d in ds if d.get("group_analysis", True)]
    online_edges = [e for e in es if e["last_seen"] and now - e["last_seen"] < 2 * 3600_000]
    online_devices = [d for d in ds if d["online"]]

    today = dt.date.today()
    week_ago = (today - dt.timedelta(days=6)).isoformat()
    rows = [r for r in store.daily_rows(4000) if r["day"] >= week_ago]
    # 完整率按「设备日」算：一台设备一天有数据才算完整，缺一台也能看见
    device_days = {(r["device_id"], r["day"]) for r in rows}
    complete = len(device_days) / max(1, len(visible_devices) * 7) * 100
    # 每天一张完整率，给总览画 7 点走势；缺口出现在哪天比一个总数有用
    days = [(today - dt.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    completeness_days = [
        round(len({r["device_id"] for r in rows if r["day"] == d}) / max(1, len(visible_devices)) * 100)
        for d in days]

    alerts = store.advice(risk="alert", limit=200)
    top: dict[str, dict] = {}
    for a in alerts:
        key = a["text"]
        top.setdefault(key, {"text": key, "count": 0, "edge": a["edge_id"],
                             "device": a["device_id"], "risk": a["risk"]})
        top[key]["count"] += 1
    alerts_top = sorted(top.values(), key=lambda x: -x["count"])[:5]

    feed = store.alert_feed(days=30)
    open_alerts = [a for a in feed if a["state"] in ("pending", "acked", "muted")]
    pending_alerts = [a for a in open_alerts if a["state"] == "pending"]
    critical = sum(1 for a in pending_alerts if a["severity"] == "critical")
    notice = sum(1 for a in pending_alerts if a["severity"] == "notice")
    status_level = "critical" if critical else "notice" if notice else "ok"

    latest = store.reports(1)
    counter = store.counters()
    # 文档 §6.2：原始字段上报要「拒收并告警」，不能只留一个计数
    system_alerts = []
    if store.audit_since("ingest.reject", now - 86_400_000):
        system_alerts.append({"scope": "cloud", "kind": "raw_payload", "severity": "critical",
                              "text": "近 24 小时有原始字段上报被拒收"})
    return {
        "status": {
            "level": status_level,
            "critical": critical,
            "notice": notice,
            "acked": sum(1 for a in open_alerts if a["state"] == "acked"),
            "muted": sum(1 for a in open_alerts if a["state"] == "muted"),
            "stale_edges": sum(1 for a in pending_alerts if a["kind"] == "stale"),
            "top": pending_alerts[:3],
        },
        "completeness_delta": round(completeness_days[-1] - sum(completeness_days[:-1]) / 6, 1) if completeness_days else 0,
        "cloud_id": config.CLOUD_ID, "cloud_name": config.CLOUD_NAME,
        "generated_at": now,
        "edges_total": len(es), "edges_online": len(online_edges),
        "devices_total": len(ds), "devices_online": len(online_devices),
        "group_analysis_off": off_edges,
        "completeness_7d": round(complete, 1),
        "completeness_days": completeness_days,
        "rows_7d": len(rows),
        "alerts_total": len(alerts),
        "alerts_top": alerts_top,
        # 系统级告警（原始字段上报被拒收等），与按设备聚合的 alerts_top 不是一回事
        "alerts": system_alerts,
        "k_min_seen": min([r["k"] for r in rows] or [0]),
        "reported_suppressed": counter.get("reported_suppressed", 0),
        "reported_raw_rejected": counter.get("reported_raw_rejected", 0),
        "latest_ai": latest[0] if latest else None,
        "ai_mode": config.AI_MODE,
        "edges": es,
    }


@router.get("/alerts", dependencies=admin)
def alerts(days: int = Query(default=30, ge=1, le=90),
           state: str = Query(default="open")) -> list[dict[str, Any]]:
    rows = store.alert_feed(days=days)
    if state == "open":
        rows = [r for r in rows if r["state"] in ("pending", "acked", "muted")]
    return rows


def _alert_key(body: dict[str, Any]) -> str:
    key = str(body.get("key") or "")
    if not key:
        raise HTTPException(422, "需要 key")
    return key


@router.post("/alerts/ack", dependencies=admin)
def ack_alert(body: dict[str, Any]) -> dict[str, Any]:
    key = _alert_key(body)
    store.set_alert_state(key, "admin", acked=True)
    return {"ok": True}


@router.post("/alerts/mute", dependencies=admin)
def mute_alert(body: dict[str, Any]) -> dict[str, Any]:
    key = _alert_key(body)
    reason = str(body.get("reason") or "").strip()
    hours = int(body.get("hours") or 0)
    if not reason:
        raise HTTPException(422, "静音需要填写理由")
    if hours <= 0:
        raise HTTPException(422, "静音需要有效期")
    store.set_alert_state(key, "admin", mute_hours=hours, reason=reason)
    return {"ok": True}


@router.post("/alerts/unmute", dependencies=admin)
def unmute_alert(body: dict[str, Any]) -> dict[str, Any]:
    key = _alert_key(body)
    store.clear_alert_mute(key)
    return {"ok": True}


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
    # 展示前再过一遍脱敏：这是兜底，不能替代端侧脱敏（见 ai.scrub）
    return [{**a, "text": ai.scrub(a["text"])} for a in store.advice(edge, risk, limit)]


@router.get("/daily/counts", dependencies=admin)
def daily_counts(days: int = Query(default=7, ge=1, le=30),
                 edge: str | None = None) -> list[dict[str, Any]]:
    return store.daily_counts(days, edge)


@router.get("/daily", dependencies=admin)
def daily(edge: str | None = None, limit: int = Query(default=40, ge=1, le=500)) -> list[dict[str, Any]]:
    return store.daily_rows(limit, edge)


@router.get("/compare", dependencies=admin)
def compare(metric: str, scope: str = Query(default="edges", pattern="^(edges|devices)$")) -> dict[str, Any]:
    """同一指标横向对比：多边或多设备，都以首日为 100。"""
    rollup = queue.cache_get("rollup:edges") or {}
    if scope == "devices":
        out = []
        for d in store.devices():
            if not d.get("group_analysis", True):
                continue
            pts = store.series(d["edge_id"], d["device_id"], metric)
            if not pts:
                continue
            base = pts[0]["mean"] or 1
            out.append({
                "edge_id": d["edge_id"], "device_id": d["device_id"],
                "name": d["device_id"],
                "points": [{"day": p["day"], "value": p["mean"],
                            "index": round(p["mean"] / base * 100, 1)} for p in pts],
                "k_min": min(p["k"] for p in pts),
            })
        days = sorted({p["day"] for s in out for p in s["points"]})
        return {"metric": metric, "scope": "devices", "devices": out,
                "days": days, "rollup": {}}

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
    return {"metric": metric, "scope": "edges", "edges": out, "days": days,
            "rollup": (rollup.get("edges") or {}) if isinstance(rollup, dict) else {}}


# ---------------------------------------------------------------- AI 报告

@router.get("/ai/reports", dependencies=admin)
def ai_reports(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    return store.reports(limit)


@router.get("/ai/reports/{report_id}", dependencies=admin)
def ai_report(report_id: int) -> dict[str, Any]:
    r = store.report(report_id)
    if not r:
        raise HTTPException(404, "报告不存在")
    r["content"] = ai.scrub_content(r["content"])
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
    # 模式 A 会把 L2 发去公有 API，不能让一次请求就切过去
    if "mode" in body and str(mode) != "rules" and not config.ALLOW_MODE_SWITCH:
        raise HTTPException(400, "模式 A 必须由部署方在配置里开启"
                                 "（CLOUD_ALLOW_MODE_SWITCH=1）；接口只能切到 rules")
    task = body.get("task", "weekly_narrative")
    if task not in ai.TASKS:
        raise HTTPException(400, f"task 只能是 {'/'.join(ai.TASKS)}")
    # 文档 §6.5：POST 可带 period（from/to），不传就用最近 7 天
    period = body.get("period")
    if period is not None:
        try:
            start, end = dt.date.fromisoformat(period["from"]), dt.date.fromisoformat(period["to"])
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(400, "period 必须是 {from: YYYY-MM-DD, to: YYYY-MM-DD}") from e
        if start > end:
            raise HTTPException(400, "period.from 不能晚于 period.to")
        period = [start.isoformat(), end.isoformat()]
    # 文档 §6.1：AI 报告在 worker 里生成。等不及就返回 job_id，前端再查。
    out = queue.submit("generate_report", scope, scope_id, task, mode, period, wait=120)
    if out["status"] == "done":
        result = dict(out["result"])
        result["content"] = ai.scrub_content(result.get("content") or {})
        return result
    if out["status"] == "failed":
        raise HTTPException(500, f"报告生成失败：{out.get('error', '')}")
    return {"queued": True, "job_id": out["job_id"], "scope": scope, "task": task}


# ---------------------------------------------------------------- 隐私审计

@router.get("/privacy/audit", dependencies=admin)
def privacy_audit() -> dict[str, Any]:
    today = dt.date.today()
    rows = store.daily_rows(4000)
    ks = [r["k"] for r in rows]
    dist: dict[str, int] = {}
    for k in ks:
        # k<5 不应该存在；真出现了要单独看见，不能并进「5」里
        b = "<5" if k < 5 else "5" if k == 5 else "6-7" if k <= 7 else "8-9" if k <= 9 else "10+"
        dist[b] = dist.get(b, 0) + 1
    c = store.counters()
    return {
        "k_min": config.K_MIN,
        "rows": len(rows),
        "k_distribution": dist,
        "k_min_seen": min(ks) if ks else 0,
        # 云侧自己拦的 vs 边自报的，分开写：混在一起就没法证明「0 漏网」
        "raw_rejected_local": c.get("raw_rejected_local", 0),
        "reported_suppressed": c.get("reported_suppressed", 0),
        "reported_raw_rejected": c.get("reported_raw_rejected", 0),
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
    counters = store.counters()
    try:
        with store.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        db = "ok"
    except Exception:  # noqa: BLE001
        db = "fail"
    return {
        "ok": db == "ok", "cloud_id": config.CLOUD_ID, "db": db,
        "demo": config.DEMO, "build": config.BUILD,
        "edge_token": "set" if config.EDGE_TOKEN else "open",
        "admin_token": "set" if config.ADMIN_TOKEN else "open",
        "ai_mode": config.AI_MODE,
        # 不再回 Ollama 地址、队列名这类内网拓扑，扫描者拿不到攻击线索
        "gpu": gpu.status(),
        "model": ai.model_status() if config.AI_MODE == "local" else None,
        "edges": len(store.edges()), "devices": len(store.devices()),
        "ingested_rows": counters.get("rows_ingested", 0),
        "auth_required": auth.required(),
        "api_tokens_used": counters.get("api_tokens", 0),
        "api_budget": config.API_BUDGET_TOKENS,
        "mdns": {"on": mdns.mdns.status().get("on", False)},
        # 备份与演练的结果要能被看见，不然「每周加密」失败了也没人知道
        "backup": {
            "ok": counters.get("backup_ok", 0),
            "fail": counters.get("backup_fail", 0),
            "encrypt_fail": counters.get("backup_encrypt_fail", 0),
            "drill_ok": counters.get("backup_drill_ok", 0),
            "drill_fail": counters.get("backup_drill_fail", 0),
        },
    }
