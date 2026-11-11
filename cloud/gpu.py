"""GPU 自检（文档 §6.8）：不锁厂商，NVIDIA 与 AMD 都能识别。

只做检测，不装驱动、不启容器。AMD 走 ROCm 的 rocm-smi，NVIDIA 走 nvidia-smi；
两者都没有就报 none，让 AI 走规则模板而不是假装可用。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _run(args: list[str]) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=4)
        return (out.stdout or "").strip().splitlines()[0] if out.stdout else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def status() -> dict:
    if shutil.which("nvidia-smi"):
        name = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        if name:
            return {"vendor": "nvidia", "name": name, "ok": True, "api": "cuda"}
    if shutil.which("rocm-smi"):
        line = _run(["rocm-smi", "--showproductname"])
        if line:
            return {"vendor": "amd", "name": line, "ok": True, "api": "rocm"}
    # 容器里通常没有驱动工具，只映射了设备节点时也给个线索
    nodes = [p for p in ("/dev/kfd", "/dev/dri") if Path(p).exists()]
    if nodes:
        return {"vendor": "unknown", "name": "", "ok": False,
                "api": "unknown", "nodes": nodes}
    return {"vendor": "none", "name": "", "ok": False, "api": "none"}
