"""云节点配置。单租户、单机，全部走环境变量。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


DATA_DIR = Path(_env("CLOUD_DATA_DIR", str(ROOT / "data")))
DB_PATH = Path(_env("CLOUD_DB", str(DATA_DIR / "cloud.db")))

CLOUD_ID = _env("CLOUD_ID", "cloud-home-01")
CLOUD_NAME = _env("CLOUD_NAME", "私有云 · 家庭实验室")

# 边上报令牌；公网部署必须设置，见 README「安全边界」。
EDGE_TOKEN = _env("CLOUD_EDGE_TOKEN")
ADMIN_TOKEN = _env("CLOUD_ADMIN_TOKEN")

K_MIN = int(_env("CLOUD_K_MIN", "5"))
RETENTION_DAYS = int(_env("CLOUD_RETENTION_DAYS", "730"))

# 模式 B（默认）：本地 Ollama；模式 A：公有 API，默认关。
AI_MODE = _env("CLOUD_AI_MODE", "local")
OLLAMA_URL = _env("CLOUD_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = _env("CLOUD_OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
PUBLIC_API_KEY = _env("CLOUD_PUBLIC_API_KEY")
PUBLIC_API_URL = _env("CLOUD_PUBLIC_API_URL", "")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
