"""管理员登录：口令 + TOTP，会话是 HMAC 签名的 cookie。

单租户、一个管理员账号。没设口令时（本机演示）整个模块是空转，
/api/v1/auth/state 会告诉前端「不需要登录」。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from typing import Any

from . import config, store

COOKIE = "mt_session"
SESSION_TTL = 12 * 3600
STEP = 30  # TOTP 步长

# 登录失败锁定：单 IP 10 分钟内错 5 次就等窗口过去；全站再留一个很宽的兜底。
# 进程内记账够用（单容器单管理员），换 IP 重试也照样受限。
FAIL_MAX = 5
FAIL_WINDOW = 600
GLOBAL_FAIL_MAX = 50
_fails: dict[str, list[float]] = {}


def _prune_fails(now: float) -> None:
    cutoff = now - FAIL_WINDOW
    for ip in list(_fails):
        _fails[ip] = [t for t in _fails[ip] if t >= cutoff]
        if not _fails[ip]:
            del _fails[ip]


def _wait(times: list[float], limit: int, at: float) -> int:
    if len(times) < limit:
        return 0
    return max(1, int(FAIL_WINDOW - (at - times[0])))


def locked_for(ip: str = "", now: float | None = None) -> int:
    """还要锁多少秒；0 表示没锁。"""
    at = time.time() if now is None else now
    _prune_fails(at)
    wait = _wait(_fails.get(ip or "-", []), FAIL_MAX, at)
    if wait:
        return wait
    total = [t for times in _fails.values() for t in times]
    return _wait(sorted(total), GLOBAL_FAIL_MAX, at)


def reset_lockout() -> None:
    _fails.clear()


# ---------------------------------------------------------------- TOTP

def totp(secret: str, at: int | None = None, step: int = STEP, digits: int = 6) -> str:
    """RFC 6238（HMAC-SHA1），与手机上的验证器一致。"""
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    counter = int((at if at is not None else time.time()) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def totp_step(secret: str, code: str, at: int | None = None, window: int = 1) -> int | None:
    """命中的时间步号；返回步号而不是 bool，才能挡住同一步的第二次登录。"""
    now = at if at is not None else int(time.time())
    for k in range(-window, window + 1):
        step = now // STEP + k
        if hmac.compare_digest(totp(secret, step * STEP), code):
            return step
    return None


def verify_totp(secret: str, code: str, at: int | None = None, window: int = 1) -> bool:
    return totp_step(secret, code, at, window) is not None


# ---------------------------------------------------------------- 会话

def required() -> bool:
    return bool(config.ADMIN_PASSWORD)


def _sign(payload: str) -> str:
    key = (config.SESSION_KEY or config.ADMIN_PASSWORD).encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:32]


def issue_session(now: int | None = None) -> str:
    body = json.dumps({"sub": "admin", "exp": (now or int(time.time())) + SESSION_TTL},
                      separators=(",", ":"))
    raw = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{raw}.{_sign(raw)}"


def check_session(token: str | None, now: int | None = None) -> bool:
    if not token or "." not in token:
        return False
    raw, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(raw)):
        return False
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError):
        return False
    return body.get("sub") == "admin" and body.get("exp", 0) > (now or int(time.time()))


def check_password(password: str) -> bool:
    if not config.ADMIN_PASSWORD:
        return True
    return hmac.compare_digest(password, config.ADMIN_PASSWORD)


def login(password: str, code: str, ip: str = "") -> dict[str, Any]:
    if not required():
        return {"ok": True, "token": issue_session()}
    wait = locked_for(ip)
    if wait:
        store.audit("anon", "auth.login", "admin",
                    {"result": "locked", "wait": wait, "ip": ip})
        return {"ok": False, "error": f"失败次数过多，{wait // 60 + 1} 分钟后再试", "wait": wait}

    def fail(reason: str, message: str) -> dict[str, Any]:
        _fails.setdefault(ip or "-", []).append(time.time())
        store.audit("anon", "auth.login", "admin",
                    {"result": reason, "ip": ip, "window": FAIL_WINDOW})
        return {"ok": False, "error": message}

    if not check_password(password):
        return fail("bad_password", "口令不正确")
    if not config.ADMIN_TOTP:
        # 文档 §6.5：管理员账号 + TOTP；只配口令等于少一道因子
        return fail("totp_missing", "管理员未配置 TOTP，已拒绝仅口令登录")
    step = totp_step(config.ADMIN_TOTP, code)
    if step is None:
        return fail("bad_totp", "动态码不正确或已过期")
    # 同一时间步只成功一次：抓到一个码也无法在 30 秒窗口里重放
    if step <= int(store.counters().get("admin_totp_step", 0)):
        return fail("totp_replay", "动态码已用过，等下一个再登录")
    store.set_counter("admin_totp_step", step)
    _fails.pop(ip or "-", None)
    store.audit("admin", "auth.login", "admin", {"result": "ok", "ip": ip})
    return {"ok": True, "token": issue_session()}


def state(token: str | None) -> dict[str, Any]:
    return {
        "required": required(),
        "totp": bool(config.ADMIN_TOTP),
        "authed": (not required()) or check_session(token),
    }


def secret_hint() -> str:
    """首次部署时用来生成 TOTP 密钥；只打印到服务端日志，不进接口。"""
    return base64.b32encode(os.urandom(10)).decode().rstrip("=")
