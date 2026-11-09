"""任务队列与缓存：Redis + RQ。

文档 §6.1 的分工是 api 只接请求、worker 干活。没有 Redis（本机调试、
单元测试）时 CLOUD_QUEUE_SYNC=1 让任务就地执行，调用方代码不用分叉。
"""
from __future__ import annotations

import json
from typing import Any

from . import config

QUEUE_NAME = "cloud"


def redis_conn():
    """队列用的连接。必须是二进制模式（RQ 存的是 pickle），
    也不要设 socket_timeout —— 阻塞出队会等更久。"""
    from redis import Redis
    return Redis.from_url(config.REDIS_URL, decode_responses=False)


def cache_conn():
    """缓存/健康检查用的连接：短超时，Redis 挂了页面不能跟着卡。"""
    from redis import Redis
    return Redis.from_url(config.REDIS_URL, decode_responses=True, socket_timeout=2)


def queue():
    from rq import Queue
    return Queue(QUEUE_NAME, connection=redis_conn(), default_timeout=300)


def enqueue(name: str, *args: Any, **kwargs: Any) -> Any:
    """返回 RQ Job，或（同步模式/Redis 不可用时）函数结果本身。"""
    from . import jobs

    fn = jobs.JOBS[name]
    if config.QUEUE_SYNC:
        return fn(*args, **kwargs)
    try:
        return queue().enqueue(fn, *args, **kwargs)
    except Exception:  # noqa: BLE001 —— Redis 挂了也不能让页面跟着挂
        return fn(*args, **kwargs)


def submit(name: str, *args: Any, wait: int = 120, **kwargs: Any) -> dict[str, Any]:
    """提交任务，最多等 wait 秒。返回 {status, result} 或 {status, job_id}。"""
    from rq.job import Job

    out = enqueue(name, *args, **kwargs)
    if not isinstance(out, Job):
        return {"status": "done", "result": out}
    done = out.latest_result(timeout=wait)
    if done is None:
        return {"status": "queued", "job_id": out.id}
    if done.exc_string:
        return {"status": "failed", "job_id": out.id,
                "error": str(done.exc_string)[:300]}
    return {"status": "done", "result": done.return_value}


# ---------------------------------------------------------------- 缓存

def cache_get(key: str) -> Any | None:
    try:
        raw = cache_conn().get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def cache_set(key: str, value: Any, ttl_s: int = 600) -> None:
    try:
        cache_conn().set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl_s)
    except Exception:  # noqa: BLE001
        pass


def stats() -> dict[str, Any]:
    """给 /health 用：队列长度、Redis 是否可达。"""
    try:
        from rq import Worker
        conn = redis_conn()
        conn.ping()
        q = queue()
        workers = Worker.all(connection=conn)
        return {"redis": "ok", "queued": len(q),
                "workers": len(workers),
                "worker_names": [w.name for w in workers][:5]}
    except Exception as e:  # noqa: BLE001
        return {"redis": "fail", "error": str(e)[:120]}
