"""测试用的 Postgres 实例：管道里带一份二进制，不需要 root 或 docker。

每个测试进程起一个临时实例，跑完随进程退出。装了 docker 的环境也可以
用 CLOUD_PG_DSN 指到别处，这个模块就不会启动本地实例。
"""
from __future__ import annotations

import os
import tempfile


def ensure_pg() -> str:
    """返回可用的 DSN，并把它写进环境变量（cloud.config 之前调用）。"""
    dsn = os.environ.get("CLOUD_PG_DSN")
    if dsn:
        return dsn
    import pgserver

    data = tempfile.mkdtemp(prefix="cloud-pg-")
    server = pgserver.get_server(data)
    os.environ["CLOUD_PG_DSN"] = server.get_uri()
    os.environ.setdefault("CLOUD_QUEUE_SYNC", "1")
    return os.environ["CLOUD_PG_DSN"]
