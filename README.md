# Modular Terminal Cloud

端-边-云三层里的**云**：一台内网私有云。接收多个边节点上报的**日聚合**，
做跨边对比、AI 解读和隐私审计。方向相反——它不看单个用户，只看群体。

![总览](docs/screenshots/overview.png)

## 每个页面回答一个问题

| 页面 | 它回答的问题 | 关键内容 |
|---|---|---|
| 总览 | 这一周三条边都健康吗？ | 在线数、设备日完整率、本周 AI 摘要、告警 Top 5、边节点表 |
| 边与设备 | 问题出在哪个边、哪台设备？ | 两级下钻：边 → 设备 → 日聚合趋势与明细 |
| 对比 | 谁跑得不一样？ | 同指标跨边指数（首日 = 100）、末日指数与变化 |
| AI 报告 | 机器怎么看这一周？ | 报告列表、内容、findings、动作、模型与 token |
| 隐私审计 | 云到底拿到了什么？ | k 值分布、被抑制桶数、原始字段拦截、字段白名单、审计日志 |

![对比](docs/screenshots/compare.png)
![隐私审计](docs/screenshots/privacy.png)

## 快速开始

```bash
python3 -m pip install -r requirements.txt
./run.sh                       # 首次自动写入 3 个边 × 3 台设备 × 14 天演示数据
# http://127.0.0.1:8002
```

Docker（含可选 Ollama）：

```bash
cd docker && docker compose up -d --build
```

AI 默认 `CLOUD_AI_MODE=rules`（规则模板，零依赖）。
想用本地模型就起 Ollama 并设 `CLOUD_AI_MODE=local`；模型不可用时自动退回规则模板，
报告永远不会是空的。

## 数据流

```
边节点 ── POST /api/v1/ingest/edge-report ──▶ 云
              │  出现 user_id / subject / values 直接 422
              │  telemetry_daily 里任何一行 k<5 整份拒收
              ▼
        telemetry_daily   日聚合，唯一的事实来源
        advice            建议文本
        word_freq         词频
        ai_reports        AI 解读（同输入 24 h 内复用）
        audit             每次摄取与导出留痕
```

幂等键是 `(edge_id, report_id)`。重复投递返回 `duplicate: true` 且不写库。

## 隐私边界

云端**没有**原始记录，也没有假名 subject：

| 云端有 | 云端没有 |
|---|---|
| 日聚合 `mean / slope / z / n_samples / k` | 逐条读数 |
| 建议文本 | 用户名、用户 ID |
| 抑制计数（只有数量） | 被抑制的内容 |
| 词频 | 设备序列号、地理位置 |

`k < 5` 的行在端侧已经不产生，边也不会转发；云侧再校验一次，出现即整份拒收并记入
`raw_rejected`。全部计数在「隐私审计」页可见。

## API

完整定义见 [`contracts/openapi/cloud.yaml`](contracts/openapi/cloud.yaml)，
运行时 Swagger 在 `/api/docs`。

公网只应暴露两条：`/api/v1/ingest/edge-report` 与 `/api/v1/health`。
管理台与 Ollama 仅内网——`docker/Caddyfile` 就是这么配的，其余路径直接 404。

## 契约

| 契约 | 位置 | 谁拥有 |
|---|---|---|
| `orpi.edge.report.v1`（边→云） | `contracts/vendor/edge/edge-report@v1.json` | 边仓，本仓 vendor |
| `ai.report.v1`（云内） | `contracts/ai/report.v1.json` | **本仓** |

`python3 scripts/sync_contracts.py --check` 校验 vendor 副本没漂移。

## 测试

```bash
python3 -m unittest discover -s tests -t . -v
```

8 个用例：边报表示例必须能入库、重复必须幂等、原始字段与 k<5 必须被拒、
审计页的 k 分布必须等于总行数、AI 报告必须通过 `ai.report.v1` 校验、
同一输入第二次生成必须命中缓存。

## 与方案 v2.2 的差异

| 方案里的东西 | 现状 |
|---|---|
| PostgreSQL 16 + Redis 7 + RQ worker | 用 SQLite 单机替代。表结构与 DDL 一一对应，换 Postgres 只需改 `store.py` |
| 管理员账号 + TOTP 登录 | 未实现；`CLOUD_ADMIN_TOKEN` 是唯一的门 |
| 边证书 mTLS | 未实现，用 Bearer token；Caddy 的 TLS 已配好，接 client cert 只差一行 |
| 公有 API 模式（DeepSeek / qwen-plus） | 只留了配置位与 `PUBLIC_API_KEY`，未实现调用路径 |
| 词频 jieba 分词 | 用受控词表精确匹配（`ai.py` 的 `LEXICON`），换模板要同步改词表 |
| 每日 pg_dump + 加密离线副本 | 只提供 `scripts/backup.sh`（SQLite 在线备份，保留 14 天） |

演示数据由 `cloud/seed.py` 生成，**不是真实上报**；接真机前清掉 `data/cloud.db`。

## 目录

```
cloud/         API、存储、AI 报告、演示数据、前端静态资源
contracts/     ai 契约、示例、OpenAPI、vendor 副本
tests/         摄取 / AI 两组用例
docker/        compose、Caddyfile、Dockerfile 说明
scripts/       vendor 契约同步、备份
docs/          截图
```
