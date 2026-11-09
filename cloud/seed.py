"""云演示数据：3 个边 × 3 台设备 × 14 天日聚合。"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
from typing import Any

from . import store

EDGES = [
    ("edge-lab-01", "实验室边节点", ["mt-3b-01", "mt-a64-02", "mt-rk64-03"]),
    ("edge-green-02", "温室边节点", ["mt-g-01", "mt-g-02", "mt-g-03"]),
    ("edge-home-03", "家庭边节点", ["mt-h-01", "mt-h-02", "mt-h-03"]),
]

DEVICES = {
    "mt-3b-01": ("诊室屏 · Pi 3B", "health", ["bp.systolic", "spo2", "weight"]),
    "mt-a64-02": ("温室无头 · Pine64", "env", ["env.temp", "env.humidity", "env.co2"]),
    "mt-rk64-03": ("配电无头 · Rock64", "energy", ["energy.load", "energy.pv"]),
    "mt-g-01": ("苗床探头 · A", "env", ["env.temp", "env.humidity"]),
    "mt-g-02": ("苗床探头 · B", "env", ["env.temp", "env.co2"]),
    "mt-g-03": ("灌溉电表", "energy", ["energy.load"]),
    "mt-h-01": ("起居屏 · Pi 4", "health", ["bp.systolic", "spo2"]),
    "mt-h-02": ("客厅无头", "env", ["env.temp", "env.humidity"]),
    "mt-h-03": ("入户电表", "energy", ["energy.load", "energy.pv"]),
}

BASE = {
    "bp.systolic": 129.0, "spo2": 97.1, "weight": 68.4,
    "env.temp": 22.6, "env.humidity": 53.0, "env.co2": 640.0,
    "energy.load": 1.35, "energy.pv": 2.4,
}
TREND = {  # 每天的整体漂移，让三条边走势不同
    "bp.systolic": 0.55, "spo2": -0.06, "weight": -0.03,
    "env.temp": 0.12, "env.humidity": -0.4, "env.co2": 6.0,
    "energy.load": 0.03, "energy.pv": -0.06,
}

ADVICE = [
    ("近 7 天收缩压缓慢上升，建议复测并关注晨间读数", "watch", 0.72),
    ("收缩压连续三个窗口高于基线，建议复测并与医生确认", "alert", 0.81),
    ("血氧平稳，未见连续偏低", "info", 0.78),
    ("午后 CO₂ 连续偏高，建议换气通风", "watch", 0.69),
    ("CO₂ 出现尖峰，与人员集中时段吻合", "alert", 0.74),
    ("相对湿度偏高，建议通风降低结露风险", "watch", 0.61),
    ("室内温度日间波动较大，注意结露风险", "watch", 0.64),
    ("用电负荷尖峰前移，峰谷差扩大", "watch", 0.63),
    ("光伏午间出力回落，较昨日低 0.4 kW", "watch", 0.6),
    ("体重一周内变化在正常范围，趋势平稳", "info", 0.7),
]

LEXICON = ["收缩压", "血氧", "晨间", "复测", "基线", "连续偏高", "波动", "室内温度",
           "相对湿度", "通风", "结露", "co2", "二氧化碳", "换气", "用电负荷", "光伏",
           "峰谷", "夜间", "午间", "尖峰", "回落", "关注", "建议", "异常", "趋势",
           "平稳", "偏高", "偏低"]


def generate(days: int = 14) -> dict[str, int]:
    store.init_db()
    today = dt.date.today()
    rnd = random.Random(20261109)
    rows = 0
    adv_rows = 0
    suppressed = 0
    words: dict[tuple[str, str, str], int] = {}

    with store.connect() as conn:
        conn.execute("DELETE FROM telemetry_daily")
        conn.execute("DELETE FROM advice")
        conn.execute("DELETE FROM word_freq")
        conn.execute("DELETE FROM devices")
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM counters")
        conn.execute("DELETE FROM ai_reports")
        conn.execute("DELETE FROM audit")
        conn.execute("DELETE FROM reports")

    for edge_id, edge_name, devs in EDGES:
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO edges(edge_id, name, cert_fp, last_seen, version) VALUES(?,?,?,?,?)",
                (edge_id, edge_name, hashlib.sha256(edge_id.encode()).hexdigest()[:16],
                 store.now_ms(), "1.0.0"))
            for d in devs:
                name, scene, metrics = DEVICES[d]
                conn.execute(
                    """INSERT INTO devices(device_id, edge_id, scene, modules_json, last_seen, online)
                       VALUES(?,?,?,?,?,1)""",
                    (d, edge_id, scene, "[]", store.now_ms()))

        for day_index in range(days):
            day = (today - dt.timedelta(days=days - 1 - day_index)).isoformat()
            weekday = dt.date.fromisoformat(day).weekday()
            for d in devs:
                _name, scene, metrics = DEVICES[d]
                # 少量缺报：完整率不会永远是 100%
                if random.Random(f"gap|{d}|{day}").random() < 0.004:
                    continue
                for metric in metrics:
                    # 端侧丢弃的是单个 5 分钟窗口，日聚合仍由其余窗口构成；
                    # 云只知道「这一天这个指标抑制了几个窗口」，不知道被抑制的内容。
                    if random.Random(f"sup|{d}|{metric}|{day}").random() < 0.03:
                        suppressed += 1
                    r = random.Random(f"{d}|{metric}|{day}")
                    drift = TREND[metric] * day_index
                    weekly = 1.0 + (0.04 if weekday >= 5 and scene != "health" else 0)
                    value = (BASE[metric] + drift) * weekly + r.gauss(0, BASE[metric] * 0.012)
                    if metric == "energy.pv":
                        value = max(0.2, value)
                    z = r.gauss(0, 0.6)
                    if r.random() < 0.03:
                        z = r.choice([-1, 1]) * r.uniform(2.6, 3.6)
                    k = 6 + r.randint(0, 4)
                    with store.connect() as conn:
                        conn.execute(
                            """INSERT OR REPLACE INTO telemetry_daily
                               (edge_id, device_id, day, metric, n_subjects, n_samples,
                                mean, slope, z, k)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (edge_id, d, day, metric, k, k * 260,
                             round(value, 3), round(r.gauss(0, 0.08), 3),
                             round(z, 2), k))
                    rows += 1

                if rnd.random() < 0.5:
                    text, risk, conf = rnd.choice(ADVICE)
                    ts = int(dt.datetime.fromisoformat(day + "T12:00:00").timestamp() * 1000)
                    with store.connect() as conn:
                        conn.execute(
                            """INSERT OR IGNORE INTO advice
                               (edge_id, device_id, text, risk, confidence, source, created_at)
                               VALUES(?,?,?,?,?,?,?)""",
                            (edge_id, d, text, risk, conf, "local", ts))
                    adv_rows += 1
                    low = text.lower()
                    for w in LEXICON:
                        if w in low:
                            key = (day, edge_id, w)
                            words[key] = words.get(key, 0) + 1

    with store.connect() as conn:
        conn.executemany(
            "INSERT INTO word_freq(day, scope, word, count) VALUES(?,?,?,?)",
            [(day, scope, w, c) for (day, scope, w), c in words.items()])

    with store.connect() as conn:
        conn.execute("INSERT INTO counters(key, value) VALUES('rows_ingested', ?)", (rows,))
        conn.execute("INSERT INTO counters(key, value) VALUES('suppressed_buckets', ?)", (suppressed,))

    from . import ai
    for scope, sid in [("edge", "edge-lab-01"), ("edge", "edge-green-02"), ("fleet", None)]:
        ai.generate(scope, sid, force_rules=True)

    return {"rows": rows, "advice": adv_rows, "words": len(words),
            "days": days, "suppressed": suppressed}


def main() -> None:
    stats = generate(14)
    print(f"已写入 {stats['rows']} 行日聚合、{stats['advice']} 条建议、"
          f"{stats['words']} 条词频，覆盖 {stats['days']} 天")


if __name__ == "__main__":
    main()
