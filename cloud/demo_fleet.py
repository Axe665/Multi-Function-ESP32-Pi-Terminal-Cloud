"""演示用的终端能力表 —— 只给 seed 用。

云端在真实部署里不关心模块的能力，它只消费边上报的日聚合；
这份表的存在是为了让演示数据也能随机换模块，而不是写死三台设备。
"""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Cap:
    module_id: int
    package_id: str
    scene: str
    metrics: tuple[tuple[str, str, str, str, int], ...]
    # (metric, label, unit, table, decimals)


CAPS: dict[int, Cap] = {c.module_id: c for c in [
    Cap(80, "planter.environment", "env", (
        ("temperature_c", "温度", "°C", "samples", 1),
        ("humidity_rh", "湿度", "%", "samples", 1),
        ("light_lux", "光照", "lx", "samples", 0),
        ("battery_v", "电池电压", "V", "samples", 2),
    )),
    Cap(82, "xiaotian.fence", "env", (
        ("intrusion_count", "触发次数", "次", "readings", 0),
        ("battery_v", "电池电压", "V", "samples", 2),
    )),
    Cap(83, "health.pressure", "health", (
        ("systolic", "收缩压", "mmHg", "measurements", 0),
        ("diastolic", "舒张压", "mmHg", "measurements", 0),
        ("pulse", "脉搏", "bpm", "measurements", 0),
    )),
    Cap(129, "test.environment", "env", (
        ("temperature_c", "温度", "°C", "samples", 1),
        ("humidity_rh", "湿度", "%", "samples", 1),
        ("light_lux", "光照", "lx", "samples", 0),
    )),
    Cap(130, "test.counter", "energy", (
        ("counter_delta", "计数增量", "次", "readings", 0),
        ("counter_rate", "计数速率", "次/min", "readings", 1),
    )),
    Cap(131, "test.camera", "env", (
        ("frames", "抓拍帧数", "帧", "readings", 0),
        ("battery_pct", "电量", "%", "samples", 0),
    )),
]}

BASE = {
    "systolic": 128.0, "diastolic": 78.5, "pulse": 71.0,
    "temperature_c": 23.2, "humidity_rh": 55.0, "light_lux": 16000.0,
    "battery_v": 3.9, "battery_pct": 82.0,
    "counter_delta": 12.0, "counter_rate": 2.4,
    "intrusion_count": 0.5, "frames": 5.0,
}
TREND = {
    "systolic": 0.55, "diastolic": 0.22, "pulse": -0.08,
    "temperature_c": 0.12, "humidity_rh": -0.4, "light_lux": -120.0,
    "battery_v": -0.004, "battery_pct": -0.35,
    "counter_delta": 0.18, "counter_rate": 0.03,
    "intrusion_count": 0.01, "frames": 0.02,
}

EDGES = [
    ("edge-lab-01", "实验室边节点"),
    ("edge-green-02", "温室边节点"),
    ("edge-home-03", "家庭边节点"),
]

# 每个边一段独立 ID：实验边的三个号与真实边节点一致，方便它上报时覆盖演示数据
BOARD_IDS = {
    "edge-lab-01": ["mt-3b-01", "mt-a64-02", "mt-rk64-03"],
    "edge-green-02": ["mt-g-01", "mt-g-02", "mt-g-03"],
    "edge-home-03": ["mt-h-01", "mt-h-02", "mt-h-03"],
}


def fleet(seed: int | None = None) -> dict[str, list[dict]]:
    """每个边抽 3 台设备，每台 1–2 个模块；模块组合每次不同。"""
    rnd = random.Random(seed)
    out: dict[str, list[dict]] = {}
    for edge_id, _name in EDGES:
        pool = BOARD_IDS[edge_id][:]
        rnd.shuffle(pool)
        devices = []
        for _ in range(3):
            dev = pool.pop()
            picked = rnd.sample(sorted(CAPS), k=rnd.choice([1, 1, 2]))
            scenes = {CAPS[m].scene for m in picked}
            rows: dict[str, dict] = {}
            for mid in picked:
                for key, label, unit, table, decimals in CAPS[mid].metrics:
                    rows.setdefault(key, {"metric": key, "label": label, "unit": unit,
                                          "table": table, "decimals": decimals,
                                          "module_id": mid, "package_id": CAPS[mid].package_id})
            devices.append({
                "device_id": dev,
                "scene": scenes.pop() if len(scenes) == 1 else "mixed",
                "modules": sorted(set(picked)),
                "metrics": list(rows.values()),
            })
        out[edge_id] = devices
    return out
