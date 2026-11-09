"""云演示数据：3 个边 × 3 台设备 × 14 天日聚合。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import random
from typing import Any

from . import store

EDGES = [
    ("edge-lab-01", "实验室边节点", ["mt-3b-01", "mt-a64-02", "mt-rk64-03"]),
    ("edge-green-02", "温室边节点", ["mt-g-01", "mt-g-02", "mt-g-03"]),
    ("edge-home-03", "家庭边节点", ["mt-h-01", "mt-h-02", "mt-h-03"]),
]

# module_id 与终端仓一致：80 盆栽环境 / 83 血压 / 129 测试环境 / 130 计数器
DEVICES = {
    "mt-3b-01": ("诊室屏 · Pi 3B", "health", [83, 80],
                 ["systolic", "diastolic", "pulse", "temperature_c", "humidity_rh"]),
    "mt-a64-02": ("温室无头 · Pine64", "env", [129],
                  ["temperature_c", "humidity_rh", "light_lux"]),
    "mt-rk64-03": ("配电无头 · Rock64", "energy", [130],
                   ["counter_delta", "counter_rate"]),
    "mt-g-01": ("苗床探头 · A", "env", [129], ["temperature_c", "humidity_rh"]),
    "mt-g-02": ("苗床探头 · B", "env", [129], ["temperature_c", "light_lux"]),
    "mt-g-03": ("灌溉电表", "energy", [130], ["counter_delta"]),
    "mt-h-01": ("起居屏 · Pi 4", "health", [83], ["systolic", "pulse"]),
    "mt-h-02": ("客厅无头", "env", [129], ["temperature_c", "humidity_rh"]),
    "mt-h-03": ("入户电表", "energy", [130], ["counter_delta", "counter_rate"]),
}

BASE = {
    "systolic": 128.0, "diastolic": 78.5, "pulse": 71.0,
    "temperature_c": 23.2, "humidity_rh": 55.0, "light_lux": 16000.0,
    "counter_delta": 12.0, "counter_rate": 2.4,
}
TREND = {  # 每天的整体漂移，让三条边走势不同
    "systolic": 0.55, "diastolic": 0.22, "pulse": -0.08,
    "temperature_c": 0.12, "humidity_rh": -0.4, "light_lux": -120.0,
    "counter_delta": 0.18, "counter_rate": 0.03,
}

ADVICE = [
    ("近 7 天收缩压缓慢上升，建议复测并关注晨间读数", "watch", 0.72),
    ("收缩压连续三个窗口高于基线，建议复测并与医生确认", "alert", 0.81),
    ("脉搏与血压同步平稳，未见异常", "info", 0.78),
    ("舒张压夜间低于基线，建议关注睡眠时长", "watch", 0.63),
    ("温室内温度偏高，建议开启通风", "watch", 0.69),
    ("光照低于生长区间，建议补光", "watch", 0.66),
    ("环境湿度不足，建议补水并观察叶片", "watch", 0.67),
    ("温湿度平稳，处于适宜区间", "info", 0.8),
    ("计数速率出现尖峰，与晚间时段吻合", "alert", 0.74),
    ("夜间计数速率偏高，建议核对用电设备", "watch", 0.62),
    ("计数增量平稳，未见异常尖峰", "info", 0.77),
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
                name, scene, modules, _metrics = DEVICES[d]
                conn.execute(
                    """INSERT INTO devices(device_id, edge_id, scene, modules_json, last_seen, online)
                       VALUES(?,?,?,?,?,1)""",
                    (d, edge_id, scene, json.dumps(modules), store.now_ms()))

        for day_index in range(days):
            day = (today - dt.timedelta(days=days - 1 - day_index)).isoformat()
            weekday = dt.date.fromisoformat(day).weekday()
            for d in devs:
                _name, scene, _modules, metrics = DEVICES[d]
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
                    if metric in ("light_lux", "counter_delta", "counter_rate"):
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
