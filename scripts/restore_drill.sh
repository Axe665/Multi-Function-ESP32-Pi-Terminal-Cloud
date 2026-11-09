#!/usr/bin/env bash
# 恢复演练（文档 §6.7）：用最新 dump 恢复到一个临时库，核对行数与最新日期，然后删库。
# 用法：./scripts/restore_drill.sh ITERM  （或直接调 worker 里的同一函数）
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
import json, sys
sys.path.insert(0, ".")
from cloud import jobs
print(json.dumps(jobs.run_restore_drill(), ensure_ascii=False, default=str))
PY
