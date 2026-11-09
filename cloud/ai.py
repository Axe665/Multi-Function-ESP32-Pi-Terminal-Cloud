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


TASKS = ("weekly_narrative", "fleet_compare", "advice_digest", "wordcloud_label")


def _clip(summary: dict[str, Any], max_tokens: int = 8000) -> dict[str, Any]:
    """单次输入不超过 8k token：按字符粗估，超了就先把明细砍掉。"""
    def size(obj) -> int:
        return len(json.dumps(obj, ensure_ascii=False)) // 3

    out = dict(summary)
    while size(out) > max_tokens and out.get("metrics"):
        out["metrics"] = out["metrics"][:-1]
    while size(out) > max_tokens and out.get("advice_top"):
        out["advice_top"] = out["advice_top"][:-1]
    return out


def _summary(scope: str, scope_id: str | None, days: int = 7,
             period: tuple[str, str] | None = None) -> dict[str, Any]:
    """给模型（或规则）看的输入摘要：只有聚合，没有原始值。"""
    start, end = period if period else _window(days)
    rows = store.daily_rows(4000, scope_id if scope == "edge" else None)
    rows = [r for r in rows if start <= r["day"] <= end]
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
        # 摘要类任务要用到聚合结果
        "advice_counts": _advice_counts(scope_id),
        "words": [w["word"] for w in store.wordcloud(scope_id, days, dt.date.today().isoformat())[:8]],
        "privacy": store.counters(),
    }


def _advice_counts(scope_id: str | None) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT text, risk, COUNT(*) AS n FROM advice
               WHERE (%s::text IS NULL OR edge_id = %s)
               GROUP BY text, risk ORDER BY n DESC LIMIT 6""",
            (scope_id, scope_id)).fetchall()
    return [{"text": r["text"], "risk": r["risk"], "count": r["n"]} for r in rows]


def _by_edge() -> list[dict[str, Any]]:
    """按边聚合出各指标的均值，供 fleet_compare 用。"""
    with store.connect() as conn:
        rows = conn.execute(
            """SELECT edge_id, metric, AVG(mean) AS mean, MAX(ABS(z)) AS z
               FROM telemetry_daily GROUP BY edge_id, metric""").fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["edge_id"], {"edge_id": r["edge_id"], "metrics": []})
        out[r["edge_id"]]["metrics"].append(
            {"metric": r["metric"], "mean": round(r["mean"], 2), "z": round(r["z"], 2)})
    return list(out.values())


def prompt_hash(summary: dict[str, Any], task: str = "weekly_narrative") -> str:
    """对「输入摘要 + 任务类型」取哈希：同一份输入 24 h 内复用报告。"""
    raw = json.dumps(summary, sort_keys=True, ensure_ascii=False) + "|" + task
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _rules(summary: dict[str, Any], task: str = "weekly_narrative") -> dict[str, Any]:
    if task == "advice_digest":
        return _rules_digest(summary)
    if task == "fleet_compare":
        return _rules_compare(summary)
    if task == "wordcloud_label":
        return _rules_words(summary)
    return _rules_narrative(summary)


def _base(summary: dict[str, Any], headline: str, narrative: str,
          confidence: str = "low") -> dict[str, Any]:
    return {"schema": "ai.report.v1", "scope": summary["scope"],
            "scope_id": summary["scope_id"], "task": summary.get("task"),
            "period": {"from": summary["from"], "to": summary["to"]},
            "headline": headline, "narrative": narrative,
            "findings": [], "actions": [], "confidence": confidence}


def _rules_digest(summary: dict[str, Any]) -> dict[str, Any]:
    pools = summary.get("advice_counts") or []
    if not pools:
        return _base(summary, "本周没有需要单独处理的建议",
                     "这一周期里设备没有产出告警或关注级建议，可以按常规节奏巡检。")
    top = pools[:3]
    head = f"本周值得关注的三件事：{top[0]['text'][:18]}"
    parts = [f"这一周期共记录 {sum(p['count'] for p in pools)} 条建议，按出现次数排序："]
    for i, p in enumerate(top, 1):
        parts.append(f"{i}. {p['text']}（{p['count']} 次，{p['risk']}）")
    parts.append("同一条建议在多台设备重复出现时，通常对应一个共同的环境因素，"
                 "优先排查共性而不是逐台处理。")
    body = _base(summary, head, "".join(parts))
    body["findings"] = [{"label": p["text"][:16], "detail": f"出现 {p['count']} 次",
                         "risk": p["risk"]} for p in top]
    body["actions"] = [f"从「{top[0]['text'][:20]}」开始排查", "核对重复出现建议的设备是否在同一时段"]
    return body


def _rules_compare(summary: dict[str, Any]) -> dict[str, Any]:
    edges = summary.get("edges") or []
    if len(edges) < 2:
        return _rules_narrative(summary)
    groups = _by_edge()
    metrics = sorted({m["metric"] for g in groups for m in g["metrics"]})
    worst, worst_gap, worst_metric = None, 0.0, None
    for metric in metrics:
        vals = {m["metric"]: m["mean"] for g in groups for m in g["metrics"]}
        present = [g for g in groups if any(m["metric"] == metric for m in g["metrics"])]
        if len(present) < 2:
            continue
        means = [next(m["mean"] for m in g["metrics"] if m["metric"] == metric) for g in present]
        lo, hi = min(means), max(means)
        gap = (hi - lo) / abs(lo) if lo else 0
        if gap > worst_gap:
            worst, worst_gap, worst_metric = hi, gap, metric
    if not worst_metric:
        return _rules_narrative(summary)
    label = _label(worst_metric)
    head = f"{label}在边之间的差异最大，相差 {worst_gap * 100:.0f}%"
    body = _base(summary, head,
                 f"对比 {len(groups)} 个边的日均值，{label}的极差最为明显（最高 {worst:.1f}）。"
                 f"差异通常来自场景不同（诊室、温室、配电），先确认是否属于预期，"
                 f"再看同一场景的边之间是否也在分化。")
    body["findings"] = [{"label": label, "detail": f"极差 {worst_gap * 100:.0f}%，最高 {worst:.1f}",
                         "risk": "watch"}]
    body["actions"] = [f"展开{label}的跨边对比，确认差异来自场景还是异常"]
    return body


def _rules_words(summary: dict[str, Any]) -> dict[str, Any]:
    words = summary.get("words") or []
    if not words:
        return _base(summary, "词云还没有主题词", "这一周期没有生成足够的建议文本，"
                                                  "等第一份报表到达后再挑词。")
    head = "词云主题：" + "、".join(words[:4])
    body = _base(summary, head,
                 f"建议文本里出现最多的词依次是 {'、'.join(words[:6])}。"
                 "词云只反映设备侧建议的用词频次，不代表严重程度；"
                 "严重程度看同一份报告里的 findings。")
    body["findings"] = [{"label": w, "detail": "词频靠前", "risk": "info"} for w in words[:3]]
    return body


def _rules_narrative(summary: dict[str, Any]) -> dict[str, Any]:
    """规则模板：不调模型也能给出可读报告，confidence=low。"""
    ms = summary["metrics"]
    if not ms:
        body = _base(summary, "本周期没有可分析的聚合数据",
                     "边缘节点还没有上报 k≥5 的日聚合，等第一份报表到达后再生成解读。")
        body["actions"] = ["检查边的发件箱与云地址配置"]
        return body

    # 先看统计显著性，再看相对变化：低基数指标的百分比不该霸榜
    moved = sorted(ms, key=lambda m: (round(m["max_abs_z"], 1), abs(m["pct"])), reverse=True)
    top, second = moved[0], (moved[1] if len(moved) > 1 else None)
    hot = [m for m in ms if m["max_abs_z"] >= 2.5]

    direction = "上升" if top["delta"] >= 0 else "下降"
    # 变化量小于 1 时用百分比说，别出现"上升 0.0 次"
    if abs(top["delta"]) < 1:
        headline = f"{top['label']}本周期变化 {top['pct']:+.1f}%"
    else:
        headline = f"{top['label']}本周期{direction} {abs(top['delta']):.1f} {top['unit']}".strip()

    parts = [
        f"过去 {summary['from']} 至 {summary['to']}，共 {summary['devices']} 台设备上报了 "
        f"{sum(m['points'] for m in ms)} 个指标日聚合，覆盖 {len(ms)} 个指标。",
        f"{top['label']}的日均值从周期前段的基线变化 "
        f"{top['delta']:+.2f} {top['unit']}（{top['pct']:+.1f}%），峰值偏离 {top['max_abs_z']:.1f}σ，"
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

    body = _base(summary, headline, "".join(parts))
    body["findings"] = findings
    body["actions"] = actions[:3]
    return body


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
    for attempt in range(3):
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
            if attempt == 2:
                return None


def _ollama(summary: dict[str, Any]) -> dict[str, Any] | None:
    body = {
        "model": config.OLLAMA_MODEL,
        "prompt": PROMPT + json.dumps(summary, ensure_ascii=False),
        "stream": False,
        "format": "json",
        "options": {"num_ctx": 4096, "temperature": 0.2},
    }
    for attempt in range(3):
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
            data.setdefault("task", task)
            return data
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            if attempt == 2:
                return None


def generate(scope: str, scope_id: str | None = None, force_rules: bool = False,
             task: str = "weekly_narrative",
             period: tuple[str, str] | None = None) -> dict[str, Any]:
    if task not in TASKS:
        task = "weekly_narrative"
    summary = _summary(scope, scope_id, period=period)
    summary["task"] = task
    summary = _clip(summary)
    ph = prompt_hash(summary, task)
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
        content = _rules(summary, task)
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
                {"scope": scope, "scope_id": scope_id, "model": model, "task": task})
    return {"id": rid, "model": model, "content": validated.model_dump(by_alias=True)}
