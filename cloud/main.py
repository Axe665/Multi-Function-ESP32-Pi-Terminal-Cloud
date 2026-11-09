"""云入口：python3 -m cloud.main 或 uvicorn cloud.main:app。"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api, config, store

DESCRIPTION = """\
私有云：接收边节点上报的日聚合与建议文本，做跨边对比、AI 解读与隐私审计。

- 只接受 k≥5 的 L2 聚合；出现 userId/原始值序列即 422 并计数
- AI 默认走本地 Ollama；公有 API 需显式开启且只收 k≥5 聚合
"""


def create_app() -> FastAPI:
    store.init_db()
    if os.environ.get("CLOUD_AUTOSEED") == "1":
        from . import seed
        if not store.edges():
            seed.generate(14)
    app = FastAPI(title="Modular Terminal Cloud", version="1.0.0",
                  description=DESCRIPTION, docs_url="/api/docs", redoc_url=None)
    app.include_router(api.router)
    app.mount("/", StaticFiles(directory=config.STATIC, html=True), name="static")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("cloud.main:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", "8002")), reload=False)
