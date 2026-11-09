"""AI 分析：默认本地模型（Ollama），不可用时退回规则模板。

两种模式共用同一份输入摘要和同一份输出契约 ai.report.v1；
L1-out 只进本地模型，公有 API 只接受 k≥5 聚合（这里只会拿到日聚合）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

import httpx

from . import config, store

# 与终端仓 ModuleDataApi 的字段白名单同名
LABELS = {
    "systolic": ("收缩压", "mmHg"), "diastolic": ("舒张压", "mmHg"), "pulse": ("脉搏", "bpm"),
    "temperature_c": ("温度", "°C"), "humidity_rh": ("湿度", "%"), "light_lux": ("光照", "lx"),
    "battery_v": ("电池电压", "V"), "battery_pct": ("电量", "%"),
    "counter_delta": ("计数增量", "次"), "counter_rate": ("计数速率", "次/min"),
}


def _label(metric: str, catalog: dict | None = None) -> str:
    row = (catalog or {}).get(metric)
    if row and row.get("label"):
        return row["label"]
    return LABELS.get(metric, (metric, ""))[0]


def _unit(metric: str, catalog: dict | None = None) -> str:
    row = (catalog or {}).get(metric)
    if row and row.get("unit"):
        return row["unit"]
    return LABELS.get(metric, (metric, ""))[1]


def _window(days: int = 7) -> tuple[str, str]:
    today = dt.date.today()
    return (today - dt.timedelta(days=days - 1)).isoformat(), today.isoformat()


def _summary(scope: str, scope_id: str | None, days: int = 7) -> dict[str, Any]:
    """给模型（或规则）看的输入摘要：只有聚合，没有原始值。"""
    start, end = _window(days)
    rows = store.daily_rows(4000, scope_id if scope == "edge" else None)
    rows = [r for r in rows if r["day"] >= start]
    if scope == "device" and scope_id:
        rows = [r for r in rows if r["device_id"] == scope_id]

    # 指标的名字与单位以库里收到的为准，LABELS 只是兜底
    catalog = {r["metric"]: r for r in store.metric_labels()}
    per_metric: dict[str, list[dict]] = {}
    for r in rows:
        per_metric.setdefault(r["metric"], []).append(r)

    metrics = []
    for metric, xs in sorted(per_metric.items()):
        by_day: dict[str, list] = {}
        for x in xs:
            by_day.setdefault(x["day"], []).append(x)
        days_sorted = sorted(by_day.items())
        values = [sum(y["mean"] for y in v) / len(v) for _, v in days_sorted]
        half = max(1, len(values) // 2)
        first = sum(values[:half]) / max(1, len(values[:half]))
        last = sum(values[half:]) / max(1, len(values[half:]))
        metrics.append({
            "metric": metric,
            "label": _label(metric, catalog),
            "unit": _unit(metric, catalog),
            "mean": round(sum(values) / len(values), 3),
            "delta": round(last - first, 3),
            "pct": round((last - first) / first * 100, 1) if first else 0.0,
            "max_abs_z": round(max(abs(x["z"]) for x in xs), 2),
            "k_min": min(x["k"] for x in xs),
            "n_samples": sum(x["n_samples"] for x in xs),
            "points": len(values),
        })

    return {
        "scope": scope, "scope_id": scope_id, "from": start, "to": end,
        "edges": [e["edge_id"] for e in store.edges()],
        "devices": len(store.devices(scope_id if scope == "edge" else None)),
        "metrics": metrics,
        "advice_top": [
            {"text": a["text"], "risk": a["risk"], "device": a["device_id"]}
            for a in store.advice(scope_id if scope == "edge" else None, limit=8)
        ],
        "privacy": store.counters(),
    }


def prompt_hash(summary: dict[str, Any]) -> str:
    """只对输入摘要取哈希：同一份输入 24 h 内复用报告，不重复消耗算力。"""
    raw = json.dumps(summary, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _rules(summary: dict[str, Any]) -> dict[str, Any]:
    """规则模板：不调模型也能给出可读报告，confidence=low。"""
    ms = summary["metrics"]
    if not ms:
        return {
            "schema": "ai.report.v1", "scope": summary["scope"],
            "scope_id": summary["scope_id"],
            "period": {"from": summary["from"], "to": summary["to"]},
            "headline": "本周期没有可分析的聚合数据",
            "narrative": "边缘节点还没有上报 k≥5 的日聚合，等第一份报表到达后再生成解读。",
            "findings": [], "actions": ["检查边的发件箱与云地址配置"], "confidence": "low",
        }

    # 先看统计显著性，再看相对变化：低基数指标的百分比不该霸榜
    moved = sorted(ms, key=lambda m: (round(m["max_abs_z"], 1), abs(m["pct"])), reverse=True)
    top, second = moved[0], (moved[1] if len(moved) > 1 else None)
    hot = [m for m in ms if m["max_abs_z"] >= 2.5]

    direction = "上升" if top["delta"] >= 0 else "下降"
    headline = f"{top['label']}本周期{direction} {abs(top['delta']):.1f} {top['unit']}".strip()

    parts = [
        f"过去 {summary['from']} 至 {summary['to']}，共 {summary['devices']} 台设备上报了 "
        f"{sum(m['points'] for m in ms)} 个设备日聚合，覆盖 {len(ms)} 个指标。",
        f"{top['label']}的日均值从周期前段的基线变化 {top['delta']:+.1f} {top['unit']}"
        f"（{top['pct']:+.1f}%），峰值偏离 {top['max_abs_z']:.1f}σ，"
        f"是这一周期最值得看的一项。",
    ]
    if second:
        parts.append(
            f"其次是{second['label']}，变化 {second['delta']:+.1f} {second['unit']}。")
    if hot:
        parts.append(
            f"有 {len(hot)} 个指标出现过 |z| ≥ 2.5 的窗口，最突出的是"
            f"{hot[0]['label']}（{hot[0]['max_abs_z']:.1f}σ），建议对照设备侧建议原文复核。")

    findings = [{
        "label": m["label"],
        "detail": f"均值 {m['mean']:.1f} {m['unit']}，变化 {m['delta']:+.1f}，"
                  f"最大 {m['max_abs_z']:.1f}σ，最小 k={m['k_min']}",
        "risk": "alert" if m["max_abs_z"] >= 3 else "watch" if m["max_abs_z"] >= 2.5 else "info",
    } for m in moved[:3]]

    actions = []
    if hot:
        actions.append(f"让边节点重新生成{hot[0]['label']}的趋势与建议，确认是否持续性偏移")
    actions.append("在设备页展开最近 7 天趋势，确认工作日与周末是否分开")

    return {
        "schema": "ai.report.v1", "scope": summary["scope"], "scope_id": summary["scope_id"],
        "period": {"from": summary["from"], "to": summary["to"]},
        "headline": headline,
        "narrative": "".join(parts),
        "findings": findings, "actions": actions[:3], "confidence": "low",
    }


PROMPT = """你是端-边-云系统的数据分析助手。只根据给出的 JSON 摘要写报告，
不要编造摘要里没有的数字，不要提及任何用户名或原始读数。

输出必须是严格 JSON，字段：
{"schema":"ai.report.v1","scope":...,"scope_id":...,"period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},
 "headline":"不超过 30 字的结论","narrative":"150-300 字，中文，说明变化最大的是什么、为什么值得关注",
 "findings":[{"label":"指标名","detail":"一句话","risk":"info|watch|alert"}],
 "actions":["下一步动作，最多 3 条"],"confidence":"high|low"}

摘要：
"""


def _budget_left() -> int:
    used = store.counters().get("api_tokens", 0)
    return max(0, config.API_BUDGET_TOKENS - used)


def _public_api(summary: dict[str, Any]) -> dict[str, Any] | None:
    """模式 A：公有 API。只发 k≥5 的日聚合，且受月度预算约束。"""
    if not (config.PUBLIC_API_URL and config.PUBLIC_API_KEY):
        return None
    if _budget_left() <= 0:
        store.audit("system", "ai.budget", "public_api", {"result": "exhausted"})
        return None
    body = {
        "model": config.PUBLIC_MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(summary, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 800,
        "temperature": 0.2,
    }
    for attempt in range(2):
        try:
            with httpx.Client(timeout=30, trust_env=False) as c:
                r = c.post(config.PUBLIC_API_URL, json=body,
                           headers={"Authorization": f"Bearer {config.PUBLIC_API_KEY}"})
            r.raise_for_status()
            payload = r.json()
            text = payload["choices"][0]["message"]["content"]
            data = json.loads(text)
            data.setdefault("schema", "ai.report.v1")
            data.setdefault("scope", summary["scope"])
            data.setdefault("scope_id", summary["scope_id"])
            data.setdefault("period", {"from": summary["from"], "to": summary["to"]})
            data.setdefault("confidence", "high")
            usage = payload.get("usage") or {}
            store.bump("api_tokens", int(usage.get("total_tokens", 0)))
            return data
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            if attempt == 1:
                return None


def _ollama(summary: dict[str, Any]) -> dict[str, Any] | None:
    body = {
        "model": config.OLLAMA_MODEL,
        "prompt": PROMPT + json.dumps(summary, ensure_ascii=False),
        "stream": False,
        "format": "json",
        "options": {"num_ctx": 4096, "temperature": 0.2},
    }
    for attempt in range(2):
        try:
            with httpx.Client(timeout=30, trust_env=False) as c:
                r = c.post(f"{config.OLLAMA_URL}/api/generate", json=body)
            r.raise_for_status()
            data = json.loads(r.json()["response"])
            data.setdefault("schema", "ai.report.v1")
            data.setdefault("scope", summary["scope"])
            data.setdefault("scope_id", summary["scope_id"])
            data.setdefault("period", {"from": summary["from"], "to": summary["to"]})
            data.setdefault("confidence", "high")
            return data
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            if attempt == 1:
                return None


def generate(scope: str, scope_id: str | None = None, force_rules: bool = False) -> dict[str, Any]:
    summary = _summary(scope, scope_id)
    ph = prompt_hash(summary)
    if not force_rules:
        hit = store.find_report(scope, scope_id, summary["to"], ph,
                                store.now_ms() - 24 * 3600_000)
        if hit:
            return {"id": hit["id"], "model": hit["model"], "cached": True,
                    "content": (store.report(hit["id"]) or {}).get("content", {})}
    model = "rules-v1"
    content: dict[str, Any] | None = None

    if not force_rules:
        if config.AI_MODE == "api":
            content = _public_api(summary)
            if content:
                model = config.PUBLIC_MODEL
        elif config.AI_MODE == "local":
            content = _ollama(summary)
            if content:
                model = config.OLLAMA_MODEL
    # 模型不可用、输出不合契约、或显式要求 → 规则模板，不允许空报告
    if not content:
        content = _rules(summary)
        model = "rules-v1"

    from . import models
    validated = models.AiReportContent.model_validate(content)

    tokens_in = len(PROMPT) // 2 if model != "rules-v1" else 0
    tokens_out = len(json.dumps(content, ensure_ascii=False)) // 2 if model != "rules-v1" else 0
    rid = store.save_report(
        scope, scope_id, summary["from"], summary["to"], model,
        ph, validated.model_dump(by_alias=True),
        tokens_in, tokens_out)
    store.audit("admin", "ai.report.generate", f"report:{rid}",
                {"scope": scope, "scope_id": scope_id, "model": model})
    return {"id": rid, "model": model, "content": validated.model_dump(by_alias=True)}
