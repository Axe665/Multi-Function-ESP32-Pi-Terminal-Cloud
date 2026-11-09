"""云演示数据：3 个边 × 3 台设备 × 14 天，模块组合每次重新抽。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import random

from . import demo_fleet, store

ADVICE = {
    83: [
        ("近 7 天收缩压缓慢上升，建议复测并关注晨间读数", "watch", 0.72),
        ("收缩压连续三个窗口高于基线，建议复测并与医生确认", "alert", 0.81),
        ("脉搏与血压同步平稳，未见异常", "info", 0.78),
    ],
    80: [
        ("环境湿度不足，建议补水并观察叶片", "watch", 0.67),
        ("盆土温度偏低，建议检查摆放位置", "watch", 0.64),
        ("盆栽环境平稳，与昨日同一时段接近", "info", 0.75),
    ],
    82: [
        ("红外围界夜间触发次数偏高，建议核对布防时段", "watch", 0.66),
        ("围界电池电压下降，建议更换电池", "alert", 0.71),
        ("围界运行平稳，未见异常触发", "info", 0.79),
    ],
    129: [
        ("温室内温度偏高，建议开启通风", "watch", 0.69),
        ("光照低于生长区间，建议补光", "watch", 0.66),
        ("温湿度平稳，处于适宜区间", "info", 0.8),
    ],
    130: [
        ("计数速率出现尖峰，与晚间时段吻合", "alert", 0.74),
        ("夜间计数速率偏高，建议核对用电设备", "watch", 0.62),
        ("计数增量平稳，未见异常尖峰", "info", 0.77),
    ],
    131: [
        ("抓拍帧数下降，建议检查存储空间", "watch", 0.63),
        ("相机运行平稳，电量充足", "info", 0.76),
    ],
}

LEXICON = ["收缩压", "舒张压", "血压", "脉搏", "温度", "湿度", "光照", "计数",
           "基线", "晨间", "夜间", "午后", "温室", "盆栽", "通风", "补光", "尖峰",
           "围界", "触发", "电池", "电量", "抓拍", "趋势", "平稳", "偏高", "偏低"]


def generate(days: int = 14, seed: int | None = None) -> dict[str, int]:
    store.init_db()
    today = dt.date.today()
    rnd = random.Random(seed if seed is not None else 20261109)
    fleet = demo_fleet.fleet(seed)
    rows = 0
    suppressed = 0
    adv_rows = 0
    words: dict[tuple[str, str, str], int] = {}

    with store.connect() as conn:
        for table in ("telemetry_daily", "advice", "word_freq", "devices",
                      "edges", "counters", "ai_reports", "audit", "reports"):
            conn.execute(f"DELETE FROM {table}")

    for edge_id, edge_name in demo_fleet.EDGES:
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO edges(edge_id, name, cert_fp, last_seen, version)"
                " VALUES(%s,%s,%s,%s,%s)",
                (edge_id, edge_name, hashlib.sha256(edge_id.encode()).hexdigest()[:16],
                 store.now_ms(), "1.0.0"))
            for dev in fleet[edge_id]:
                conn.execute(
                    """INSERT INTO devices(device_id, edge_id, scene, modules_json, last_seen)
                       VALUES(%s,%s,%s,%s,%s)""",
                    (dev["device_id"], edge_id, dev["scene"],
                     json.dumps(dev["modules"]), store.now_ms()))

        for day_index in range(days):
            day = (today - dt.timedelta(days=days - 1 - day_index)).isoformat()
            weekday = dt.date.fromisoformat(day).weekday()
            for dev in fleet[edge_id]:
                if random.Random(f"gap|{dev['device_id']}|{day}").random() < 0.004:
                    continue
                for row in dev["metrics"]:
                    metric = row["metric"]
                    if random.Random(f"sup|{dev['device_id']}|{metric}|{day}").random() < 0.03:
                        suppressed += 1
                    r = random.Random(f"{dev['device_id']}|{metric}|{day}")
                    drift = demo_fleet.TREND.get(metric, 0) * day_index
                    weekly = 1.0 + (0.04 if weekday >= 5 else 0)
                    value = demo_fleet.BASE.get(metric, 10) + drift + r.gauss(0, max(0.4, demo_fleet.BASE.get(metric, 10) * 0.012))
                    value = max(0.2, value * weekly if metric in ("counter_delta", "counter_rate", "intrusion_count") else value)
                    z = r.gauss(0, 0.6)
                    if r.random() < 0.03:
                        z = r.choice([-1, 1]) * r.uniform(2.6, 3.6)
                    k = 6 + r.randint(0, 4)
                    with store.connect() as conn:
                        conn.execute(
                            """INSERT INTO telemetry_daily
                               (edge_id, device_id, day, metric, n_subjects, n_samples,
                                mean, slope, z, k, module_id, label, unit, table_name)
                               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT(edge_id, device_id, day, metric) DO UPDATE SET
                                 mean=EXCLUDED.mean, slope=EXCLUDED.slope, z=EXCLUDED.z,
                                 k=EXCLUDED.k, n_subjects=EXCLUDED.n_subjects,
                                 n_samples=EXCLUDED.n_samples""",
                            (edge_id, dev["device_id"], day, metric, k, k * 260,
                             round(value, 3), round(r.gauss(0, 0.08), 3),
                             round(z, 2), k, row["module_id"], row["label"],
                             row["unit"], row["table"]))
                    rows += 1

                if rnd.random() < 0.5:
                    module_id = rnd.choice(dev["modules"])
                    pool = ADVICE.get(module_id, [("指标出现短时波动，建议对照设备侧记录复核",
                                                   "watch", 0.6)])
                    text, risk, conf = rnd.choice(pool)
                    ts = int(dt.datetime.fromisoformat(day + "T12:00:00").timestamp() * 1000)
                    with store.connect() as conn:
                        conn.execute(
                            """INSERT INTO advice
                               (edge_id, device_id, text, risk, confidence, source, created_at)
                               VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (edge_id, dev["device_id"], text, risk, conf, "local",
                             store._ms(ts)))
                    adv_rows += 1
                    low = text.lower()
                    for w in LEXICON:
                        if w in low:
                            words[(day, edge_id, w)] = words.get((day, edge_id, w), 0) + 1

    with store.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO word_freq(day, scope, word, count) VALUES(%s,%s,%s,%s)",
                [(day, scope, w, c) for (day, scope, w), c in words.items()])
        conn.execute("INSERT INTO counters(key, value) VALUES('rows_ingested', %s)", (rows,))
        conn.execute("INSERT INTO counters(key, value) VALUES('suppressed_buckets', %s)",
                     (suppressed * 12,))

    from . import ai, config
    for scope, sid in [("edge", "edge-lab-01"), ("edge", "edge-green-02"), ("fleet", None)]:
        ai.generate(scope, sid, force_rules=True)

    # 文档 §8.3/§8.5：演示环境初始化要写审计（时间、操作者、构建号）
    store.audit("system", "seed.demo", "cloud",
                {"operator": "seed", "build": config.BUILD, "days": days,
                 "rows": rows, "advice": adv_rows})
    return {"rows": rows, "advice": adv_rows, "words": len(words),
            "days": days, "suppressed": suppressed}


def main() -> None:
    stats = generate(14)
    print(f"已写入 {stats['rows']} 行日聚合、{stats['advice']} 条建议、"
          f"{stats['words']} 条词频，覆盖 {stats['days']} 天")


if __name__ == "__main__":
    main()
