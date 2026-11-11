#!/usr/bin/env bash
# AMD / ROCm 自检 + 一条最小推理（文档 §6.8）。需要 ROCm 驱动与 ollama profile。
set -euo pipefail

BASE="${CLOUD_BASE:-http://127.0.0.1:8088}"
echo "== rocm-smi"
rocm-smi --showproductname || { echo "没有 rocm-smi；先装 ROCm 驱动"; exit 1; }
echo "== /dev/kfd /dev/dri"
ls -l /dev/kfd /dev/dri 2>/dev/null || { echo "设备节点没映射进容器/宿主"; exit 1; }
echo "== ollama 进程"
docker exec mt-ollama ollama ps 2>/dev/null || echo "ollama 容器没起：docker compose --profile ai up -d"
echo "== /health"
curl -s "$BASE/api/v1/health" | python3 -c "import json,sys; h=json.load(sys.stdin); print(h.get('gpu'), h.get('model'))"
echo "== 一条报告"
"$(dirname "$0")/bench_9b.sh" fleet 1
