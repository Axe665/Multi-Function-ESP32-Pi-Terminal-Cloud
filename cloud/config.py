"""云节点配置。单租户、单机，全部走环境变量。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 前端构建产物；生产由 Caddy 服务，单进程调试时挂这里
STATIC = Path(__file__).resolve().parent.parent / "web" / "dist"


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


DATA_DIR = Path(_env("CLOUD_DATA_DIR", str(ROOT / "data")))
BACKUP_DIR = Path(_env("CLOUD_BACKUP_DIR", str(ROOT / "backups")))
# 每周离线副本的对称加密口令文件（gpg --passphrase-file）
BACKUP_KEY_FILE = Path(_env("CLOUD_BACKUP_KEY_FILE", "")) if _env("CLOUD_BACKUP_KEY_FILE") else None

# 主库：Postgres 16（文档 §6.2 的 DDL 直接可用）
PG_DSN = _env("CLOUD_PG_DSN", "postgresql://cloud:cloud@127.0.0.1:5432/cloud")
# 任务队列 + 缓存
REDIS_URL = _env("CLOUD_REDIS_URL", "redis://127.0.0.1:6379/0")
# 测试与无 Redis 的演示：任务同步执行，不进队列
QUEUE_SYNC = _env("CLOUD_QUEUE_SYNC", "0") == "1"

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
OLLAMA_MODEL = _env("CLOUD_OLLAMA_MODEL", "qwen3.5")
PUBLIC_API_KEY = _env("CLOUD_PUBLIC_API_KEY")
PUBLIC_API_URL = _env("CLOUD_PUBLIC_API_URL", "")
PUBLIC_MODEL = _env("CLOUD_PUBLIC_MODEL", "qwen3.5")
# 模式 A 的月度预算（token），超了就自动退回规则模板
API_BUDGET_TOKENS = int(_env("CLOUD_API_BUDGET_TOKENS", "200000"))

# 管理员登录：口令 + TOTP。都不设就是本机演示模式。
ADMIN_PASSWORD = _env("CLOUD_ADMIN_PASSWORD")
ADMIN_TOTP = _env("CLOUD_ADMIN_TOTP")
SESSION_KEY = _env("CLOUD_SESSION_KEY")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
