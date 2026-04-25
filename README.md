<p align="right">
  <a href="README.md">中文</a> | <a href="README.en.md">English</a>
</p>

# EvalOps

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Go 1.24](https://img.shields.io/badge/go-1.24-00ADD8.svg)](https://go.dev/)
[![License MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![buf](https://img.shields.io/badge/protocol-buf-blue)](https://buf.build)

对 Agent 和 RAG 做系统化评测：采集样本、执行任务、Judge 打分、指标与 Trace、回放坏样本。结果接进 Prometheus、Grafana 和 Jaeger。

主要面向 Agent + RAG 场景下的多跳推理等困难任务。

---

## 和现有评测框架的差异

现有评测框架主要解决"跑 benchmark 拿分数"，但两个问题没解决：

**离线评测和线上分布漂移**。产品一上线，评测集就开始过时。没有机制把线上的真实失败案例回流到评测集里。

**没有闭环**。bad case 烂在日志里，没人把它变成回归测试。下次改 prompt 或换模型，你不知道上次踩过的坑会不会再踩一次。

| 框架 | 擅长什么 | 缺什么 |
|------|---------|--------|
| OpenCompass | 模型基准评分 | Agent 行为链路评测、生产可观测 |
| lm-eval-harness | 学术 benchmark 标准化 | Trace、回归、数据飞轮 |
| DeepEval / ragas | RAG 单指标评测 | Agent trace 评测 |
| AgentBench | Agent 任务最终答案 | 只判答案对错，不看推理过程 |

EvalOps 给评测加了可观测和回归：每次 run 发 OpenTelemetry span、Prometheus 指标，bad case 回流到下一轮。

---

## 架构

```
React 前端 (Vite + Ant Design) ──REST──→ Go 控制平面 (Gin) ──gRPC──→ Python 评测引擎
  :5180                                        :8090                           │
                                                                           RunnerEngine
                                                                               │
                                                      ┌────────────────────────┼────────────────────────┐
                                                      ▼                        ▼                        ▼
                                                  RuleJudge                LLMJudge                AgentJudge
                                                  (免费、确定)             (中等成本)              (最贵、最深)
                                                      │                        │                        │
                                                      └────────────────────────┼────────────────────────┘
                                                                               ▼
                                                                          HybridJudge
                                                                      (按成本自动升级)
```

三种语言：Go（控制平面，Gin + zerolog）、Python 3.12（评测引擎，anyio + structlog + LiteLLM）、TypeScript（前端，Vite + React + Ant Design）。服务间用 gRPC 通信，Protocol Buffers 定义契约，buf 生成 stub。

---

## Judge 体系

Judge 是项目的核心。设计思路是不对所有 case 用同一个 judge，而是按成本和质量分层——便宜的 judge 能判断的就不调贵的。

### Rule Judge — 免费、确定

不调任何 LLM。按 case 类型分派：

- **RAG case**：exact match、substring match、F1、citation recall、context precision、faithfulness_lite（token-overlap 代理，不用 LLM 抓明显幻觉）
- **Agent case**：final exact match、tool selection（按序匹配期望工具）、plan efficiency（期望步数/实际步数）、error recovery
- **Chat case**：exact match、F1，兜底 `non_empty`

所有原语在 `metrics.py` 里实现，23 个单测。Rule 指标永远先跑——即使后续升级到 LLM Judge，rule 的结果也不会被丢弃。

### LLM-as-a-Judge — LiteLLM 抽象

通过 LiteLLM 调用，换 provider 就是换字符串（`openai/gpt-4o`、`anthropic/claude-sonnet-4-20250514`、`zhipu/glm-4`）。

三种子模式：

| 模式 | 工作方式 | 防止偏差 |
|------|---------|---------|
| `LLM_SINGLE` | 单模型打分，严格 JSON 输出 | `repeats` + 标准差阈值，超阈值标 `unstable` |
| `LLM_PAIRWISE` | 每个 case 调两次（SUT=A vs baseline=B，再 swap） | 位置偏差检测：两次都投 position A 赢 → 判 TIE |
| `LLM_DUAL` | 两个不同 provider 分别打分，case 级取均值 | Run 级跨所有 case 算 Cohen's κ 作为一致性代理 |

所有 LLM Judge 测试用 `StubClient`，不打真实 API。设 `EVALOPS_LLM_JUDGE=stub` 做 CI 冒烟也无需 API key。

### Agent-as-a-Judge — 四维度独立打分

多数 Agent 评测只看最终答案对不对。但 Agent 可能通过错误推理、运气或记忆"蒙对"最终答案——只看答案的 Judge 会给满分。

AgentJudge 把 Agent 的完整 ReAct trace（每一步 thought / action / observation）喂给 Judge 模型，输出四个维度的独立评分：

| 维度 | 抓什么问题 |
|------|-----------|
| `plan_quality` | 任务拆解是否合理，有没有没必要的步骤 |
| `tool_selection` | 每步选的工具对不对，参数是否合理 |
| `reasoning_coherence` | thought 是否和上一步 observation 保持一致 |
| `error_recovery` | 工具失败或返回空时，agent 有没有重试或换工具 |

每个维度 0-1 分 + 理由，`agent_judge/overall` 是加权均值。prompt 里给了每个维度的 1.0 / 0.7 / 0.4 / 0.0 锚点示例——加锚点后打分方差明显比不加锚点小。trace 渲染时剪掉超长 observation（>280 字符），整体封顶 40 步。

### Hybrid Judge — 按成本升级

```
Rule（默认全跑）→ 条件判断 → LLM → 条件判断 → Agent
```

升级条件可配置。比如 RAG case 在 `faithfulness_lite < 0.7` 或 `citation_recall < 0.5` 时升级到 LLM 层；Agent case 始终触发 Agent-as-a-Judge。LLM/Agent judge 只在第一条需要升级的 case 上才实例化，只跑 rule 的 run 不会触发 LiteLLM import。

---

## 可观测

所有内容本地运行（`make infra-up`），不依赖远程基础设施。

**指标**：Go 和 Python 各有进程私有 Prometheus registry（前缀 `evalops_cp_*` 和 `evalops_ee_*`），在同一份 Grafana 面板共存。

**链路**：`ReferenceAdapter` 每次调用注入关联头，Python 侧每个 run 套 `run_span`、每个 case 套 `case_span`，推给 Jaeger。

**Grafana**：自带 EvalOps Overview 面板，含 run 提交速率、最新通过率、成本燃烧速率、HTTP RPS、p95 延迟。

**CLI**：`evalops report <run.json>` 用 Rich 表格渲染，Docker 栈没起时这是唯一的观察界面。

访问入口：Grafana `:3001`（admin/admin）、Prometheus `:9091`、Jaeger `:16696`。

---

## SUT 接入

被测系统（System Under Test）有三种方式：

| 适配器 | 何时用 |
|--------|--------|
| `MockAdapter` | 纯内存、确定性、尊重 `rubric.mock_mode`。写单测和 CI 冒烟用，不需要任何外部服务 |
| `ReferenceAdapter` | 自己的应用提供 HTTP API 时。自动注入 `X-EvalOps-Run-Id` 等关联头 |
| Agent sidecar | 参考应用没有 Agent 接口时，独立 FastAPI 进程，ReAct executor + 4 工具 |

MockAdapter 可以确定性复现特定失败模式（`faithful` / `hallucinate` / `refuse`）。

---

## 数据集

四个 benchmark 入库：

| 名称 | 规模 | 说明 |
|------|------|------|
| `rag-toy` | 4 case | 手工 rubric，正常/幻觉/无答案/引用召回 |
| `agent-toy` | 3 case | 工具选择、plan 效率、错误恢复 |
| `hotpotqa-dev-100` | 100 case | 真实多跳 QA 基准，79 bridge + 21 comparison |
| `tau-bench-lite` | 20 case | 6 子集：单步/多跳/多工具/file_read/unanswerable/error_recovery |

---

## 快速开始

```bash
# 1. 环境
conda create -n evalops python=3.12 -y
conda activate evalops
pip install -e 'services/eval-engine[dev]'

# 2. 拉起可观测栈（可选）
make infra-up      # PG / Redis / MinIO / Jaeger / Prometheus / Grafana

# 3. 冒烟测试（不需要任何外部服务）
evalops run --benchmark datasets/rag-toy --sut mock --out runs/toy.json

# 4. 查看结果
evalops report runs/toy.json
```

更多命令：

```bash
# HotpotQA + mock SUT
evalops run --benchmark datasets/hotpotqa-dev-100 --sut mock --out runs/hotpot.json

# 通过 agent sidecar 跑 Agent 基准
scripts/deploy-sidecar.sh --reinstall
AGENT_SIDECAR_PORT=18081 agent-sidecar &
evalops run --benchmark datasets/agent-toy --sut reference \
    --sut-endpoint http://localhost:18081 --out runs/agent.json

# LLM-as-a-Judge（需要 API key）
evalops run --benchmark datasets/hotpotqa-dev-100 --sut mock \
    --judge llm_single --judge-name gpt4o --out runs/hotpot-llm.json

# Dual-judge（两个 provider，自动算 Cohen's κ）
evalops run --benchmark datasets/rag-toy --sut mock \
    --judge llm_dual --judge-name gpt4o-vs-claude --out runs/dual.json

# 断点续跑
evalops run --benchmark datasets/rag-toy --sut mock \
    --resume runs/toy.json --out runs/toy-resumed.json

# CI 冒烟（零 API key）
EVALOPS_LLM_JUDGE=stub evalops run --benchmark datasets/rag-toy \
    --sut mock --judge llm_single --out runs/stub.json
```

---

## CI

6 条并行流水线（`.github/workflows/ci.yml`），同一分支新 push 自动 cancel 旧 run：

| 流水线 | 内容 |
|--------|------|
| `eval-engine` | Python 3.11 + 3.12 矩阵，ruff check，pytest + coverage |
| `control-plane` | Go vet / build / test -race |
| `agent-sidecar` | 导入冒烟 |
| `proto` | buf lint + generate，stub 有漂移则 fail |
| `sidecar-sync` | 结构性检查 |
| `web` | npm ci + typecheck + build |

---

## CLI 参考

```
evalops run         跑一个 benchmark 打 SUT，写 Run JSON 报告
  --benchmark       benchmark 目录路径
  --sut             mock | reference
  --sut-endpoint    覆盖端点 URL
  --judge           rule | llm_single | llm_pairwise | llm_dual | hybrid
  --judge-name      run 的名称
  --concurrency     SUT 并发调用数（默认 4）
  --max-cases       只跑前 N 个 case（0=全部）
  --out             Run JSON 输出路径
  --resume          从已有 Run JSON 续跑
  --log-level       INFO | DEBUG | WARNING | ERROR

evalops report      打印已有 Run JSON（Rich 表格）
evalops show-benchmark  dump benchmark 元数据
```

---

## 本地端口

所有端口刻意避开常见默认值，两套栈可同时跑：

| 组件 | 端口 | 备注 |
|------|------|------|
| Go 控制平面 | 8090 | `/healthz`、`/metrics`、`/api/v1/runs` |
| Web 前端 | 5180 | `/api` 代理到 :8090 |
| PostgreSQL | 5452 | Docker |
| Redis | 6389 | Docker |
| Jaeger UI | 16696 | OTLP HTTP `:4328` |
| Prometheus | 9091 | Docker |
| Grafana | 3001 | admin/admin |
| Agent sidecar | 18081 | 手动启动 |

---

## License

MIT
