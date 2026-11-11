# 基准脚本（未执行）

这三份脚本对应文档 §11.3 的硬件门槛，**本机没有推理卡，全部未执行**。
脚本只负责采样与落盘，判定标准写在最后一行，不替人下结论。

| 脚本 | 用途 | 前置 |
|---|---|---|
| `bench_9b.sh [scope] [n]` | 9B 报告 p50/p95，写 `docs/bench/9b-*.json` | Ollama 已拉 `qwen3.5:9b`，云 `/health` 的 `model.reachable=true` |
| `bench_72h.sh` | 72 h 健康检查与容器内存采样，写 `docs/bench/72h-*.csv` | 边/云跑起来，采样机上有 `docker` |
| `bench_amd.sh` | ROCm 检测 + 一条最小报告 | AMD 卡、ROCm、`compose.amd.yml` |

结果模板（手动填进交付记录）：

| 日期 | 机器 | GPU / API | 模型 | 样本 | p50 | p95 | 72 h 非 200 | 备注 |
|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |

注意：

- `bench_9b.sh` 的模型是 `rules-v1` 时说明 Ollama 没起来，这次结果不能算 9B；
- 生产开了登录时，先 `export CLOUD_COOKIE='...'` 或 `CLOUD_ADMIN_TOKEN`；
- `bench_72h.sh` 不自动重启服务，掉线就是掉线，别用 watchdog 掩盖。
