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

# 登录失败锁定：10 分钟内错 5 次就等窗口过去。单管理员，进程内记着就够。
FAIL_MAX = 5
FAIL_WINDOW = 600
_fails: list[float] = []


def _prune_fails(now: float) -> None:
    cutoff = now - FAIL_WINDOW
    _fails[:] = [t for t in _fails if t >= cutoff]


def locked_for(now: float | None = None) -> int:
    """还要锁多少秒；0 表示没锁。"""
    at = time.time() if now is None else now
    _prune_fails(at)
    if len(_fails) < FAIL_MAX:
        return 0
    return max(1, int(FAIL_WINDOW - (at - _fails[0])))


def reset_lockout() -> None:
    _fails.clear()


# ---------------------------------------------------------------- TOTP

def totp(secret: str, at: int | None = None, step: int = STEP, digits: int = 6) -> str:
    """RFC 6238（HMAC-SHA1），与手机上的验证器一致。"""
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    counter = int((at or time.time()) // step)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, at: int | None = None, window: int = 1) -> bool:
    now = at or int(time.time())
    return any(hmac.compare_digest(totp(secret, now + k * STEP), code)
               for k in range(-window, window + 1))


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


def login(password: str, code: str) -> dict[str, Any]:
    if not required():
        return {"ok": True, "token": issue_session()}
    wait = locked_for()
    if wait:
        store.audit("anon", "auth.login", "admin", {"result": "locked", "wait": wait})
        return {"ok": False, "error": f"失败次数过多，{wait // 60 + 1} 分钟后再试", "wait": wait}

    def fail(reason: str, message: str) -> dict[str, Any]:
        _fails.append(time.time())
        store.audit("anon", "auth.login", "admin",
                    {"result": reason, "fails": len(_fails), "window": FAIL_WINDOW})
        return {"ok": False, "error": message}

    if not check_password(password):
        return fail("bad_password", "口令不正确")
    if config.ADMIN_TOTP:
        if not verify_totp(config.ADMIN_TOTP, code):
            return fail("bad_totp", "动态码不正确或已过期")
    _fails.clear()
    store.audit("admin", "auth.login", "admin", {"result": "ok"})
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
