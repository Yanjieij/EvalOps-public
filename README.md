# EvalOps

> [English](README.en.md) | **简体中文**

> **企业级 LLM 评测平台，自带 SRE 基因。** 把评测从一次性脚本变成常驻生产服务，驱动真正的数据飞轮。

EvalOps 是一个面向面试准备的单人项目，把一个参考企业级大模型应用包进完整的 **评测 + 可观测 + 数据飞轮** 闭环。

**项目聚焦刻意很窄：Agent + RAG，面向困难的多跳任务。** 多模态 / VLM-as-a-Judge 明确推迟到后续阶段。

## 中文简介

EvalOps 是一个面向生产场景的 LLM 评测项目，重点解决三件事：

1. 把评测从一次性脚本升级成可重复、可观测、可回归的工程系统。
2. 把 Agent 和 RAG 这两类最容易在真实业务中漂移的场景纳入统一评测链路。
3. 把坏样本、trace 和指标沉淀成下一轮回归输入，形成数据飞轮。

---

## 目录

- [为什么做 EvalOps](#为什么做-evalops)
- [四根支柱](#四根支柱)
- [架构](#架构)
- [技术栈](#技术栈)
- [特性详解](#特性详解)
  - [1. 评测 Runner](#1-评测-runner)
  - [2. Judge 引擎](#2-judge-引擎)
  - [3. SUT Adapter 与 Agent sidecar](#3-sut-adapter-与-agent-sidecar)
  - [4. 数据集](#4-数据集)
  - [5. 可观测与可视化](#5-可观测与可视化)
  - [6. Proto 契约与代码生成](#6-proto-契约与代码生成)
  - [7. CI 护栏](#7-ci-护栏)
- [仓库布局](#仓库布局)
- [快速开始](#快速开始)
- [CLI 参考](#cli-参考)
- [本地端口地图](#本地端口地图)
- [交付状态](#交付状态)
- [License](#license)

---

## 为什么做 EvalOps

市面上的开源评测框架（OpenCompass、lm-eval-harness、DeepEval、ragas、AgentBench）在"跑个基准拿个分数"这件事上已经做得很好，但它们没有解决生产 LLM 应用的两个真实痛点：

1. **离线评测和线上的分布漂移** —— 产品一上线，评测集立刻开始过时。
2. **没有闭环** —— bad case 烂在日志里，没人把它变成回归测试。

EvalOps 把评测当成 **生产服务**：有自己的 SLO、指标、追踪、CI 护栏，以及把生产失败回流到下一次回归 run 的数据飞轮管线。

## 四根支柱

| 支柱 | 定义 | 状态 |
|---|---|---|
| **可观测的评测** | 每次 run 发 OpenTelemetry span、用进程本地 Prometheus registry 暴露指标，以及 judge 一致性 / 成本 / p95 延迟 SLO | ✅ Week 1–3 |
| **Agent-as-a-Judge** | 一个 GPT-4 级别的 agent 审查 SUT agent 的完整 action trace，给出 4 个独立维度的评分：计划质量 / 工具选择 / 推理连贯 / 错误恢复 | ✅ Week 3 |
| **在线 → 离线数据飞轮** | Harvester 从生产 trace 里捞 bad case，做 PII 脱敏后回流到回归集 | ⏳ Week 4 |
| **Release Gate** | 每次应用 PR 在 GitHub Actions 里跑回归基准；未达阈值的 PR 直接被 markdown 报告挡住 | ⏳ Week 4 |

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│  前端 (Vite + React + TS + Ant Design)  ✅ 占位                 │
│  Benchmark │ Run Dashboard │ 雷达 & Diff │ Case Inspector       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ REST + SSE    (dev proxy /api → :8090)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Go 控制平面 (Gin)  ✅ 骨架                                     │
│  Request-ID │ 结构化日志 │ Prometheus │ Health 探针             │
│  鉴权 ⏳ · 调度 ⏳ · 结果 API ⏳ · SUT 注册 ⏳                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ gRPC (evalops.v1，buf 生成 ✅)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Python 评测引擎                                                │
│                                                                 │
│   Runner ✅  ── 结构化并发 + 断点续跑 + 逐 case 错误隔离        │
│     │                                                           │
│     ├── Judge ── Rule ✅ │ LLM ✅ │ Agent ✅ │ Hybrid ✅        │
│     │             │         │                                  │
│     │             │         └── LiteLLM (OpenAI/Claude/Zhipu…)  │
│     │             │                                             │
│     │             └── EM/F1/ragas-lite/tool-selection/recovery  │
│     │                                                           │
│     ├── Adapters ── Mock ✅ │ Reference HTTP ✅                 │
│     │                                                           │
│     ├── Datasets ── loader ✅ │ toy ✅ │ HotpotQA ✅ │ τ-bench ✅│
│     │                                                           │
│     └── Observability ── OTel spans ✅ │ Prometheus registry ✅ │
└─────────┬────────────────────────┬─────────────────┬───────────┘
          │                        │                 │
          ▼                        ▼                 ▼
┌──────────────────────┐ ┌───────────────────┐ ┌────────────────┐
│  Reference App (SUT) │ │ Judge 模型        │ │ 存储           │
│  chat ✅ rag ✅      │ │ via LiteLLM ✅    │ │ PG + MinIO     │
│  agent-sidecar ✅    │ │ (provider 无关)   │ │ (本地 docker)  │
└──────────────────────┘ └───────────────────┘ └────────────────┘
          ▲
          │  X-EvalOps-Run-Id / Case-Id 通过 HTTP header 传递，
          │  Week 3 已接通 OTel span 导出（设置 EVALOPS_OTEL_EXPORTER_ENDPOINT）
```

完整设计稿：[`../.claude/plans/eager-zooming-rabin.md`](../.claude/plans/eager-zooming-rabin.md)。

## 技术栈

| 层次 | 工具 | 为什么选它 |
|---|---|---|
| 控制平面 | Go 1.24 · Gin · zerolog · prometheus/client_golang | 复用成熟后端工程范式；高并发 I/O 前门 |
| 评测引擎 | Python 3.12 · Pydantic v2 · anyio · structlog · Typer · Rich | 评测是 IO-bound 的，async + 结构化并发天然合适 |
| LLM 调用 | **LiteLLM** | OpenAI / Anthropic / ZhipuAI / Gemini / Ollama / Bedrock 一套统一的 `chat.completions` API，换 provider 是**改字符串**不是改代码 |
| gRPC 契约 | Protocol Buffers · **buf 1.66** 远程插件 | 本机不需要装 `protoc`；CI 里做漂移守护 |
| 数据加载 | PyYAML + 内存 Pydantic · HotpotQA 原始 JSON 流式切片 | 小数据集以 git 友好的 YAML 入库；大的用 fetch 脚本 |
| 存储 | PostgreSQL 16（docker）· Redis 7（docker）· MinIO（docker）· ClickHouse ⏳ | Week 1 起 docker-compose 就起着让 volume 持久化；Run JSON 目前直接写盘，完整 DB 接线延后到 Week 4 |
| 可观测 | Prometheus 2.52 · Grafana 10.4 · Jaeger 1.55 · structlog ctxvars | 具体已接通的部分见 [可观测](#5-可观测与可视化) |
| 前端 | Vite 5 · React 18 · TypeScript 5 · Ant Design 5 | 当前只是占位；真正的 run dashboard 在 Week 4 |
| CI | GitHub Actions · ruff · pytest · go vet/build/race · buf lint · npm ci | 6 条独立并行，推新 commit 会 cancel 旧的 |

---

## 特性详解

### 1. 评测 Runner

**文件**：`services/eval-engine/src/evalops/runner/engine.py`

`RunnerEngine` 是把 `(benchmark, cases, SUT, judge_config)` 变成一个完整 `Run` 对象的主力。几个关键设计决策：

- **纯 async + 结构化并发**。用 `anyio.Semaphore(concurrency)` 限制并发 SUT 调用，全部放在一个 `task_group` 里。任一任务取消，其他任务干净退出。
- **逐 case 错误隔离**。单个 SUT 调用失败或 judge 报错永远不会拖垮整个 run。失败会被捕获到 `CaseResult.error`，run 仍会算出 summary。`RunStatus.FAILED`（全挂）/ `PARTIAL`（部分挂）/ `SUCCEEDED`（全绿）由错误数推导。
- **ContextVar 传播**。每个 worker 把 `run_id`、`case_id`、`request_id` 绑到 structlog 的 contextvar 里，后续任何 `log.info(...)` 都自动带上这些字段——零手动穿参。
- **Run 级指标聚合**。`_summarize` 把每个 case 的指标平均到 `RunSummary.metrics`，累加成本，算通过率；对 dual-judge run，它会遍历所有 case 的 `judge_trace["dual_raw_pairs"]`，**在整个 corpus 上只算一次 Cohen's κ**（κ 是 corpus 级统计量，单点无意义）。
- **幂等断点续跑**。`RunnerEngine(..., resume_from=prior_run)` 把已完成且无错误的 case 原样继承，只跑剩余的；并且保留原 `run_id`，让 Jaeger trace 关联跨重启存活。全量续跑（所有 case 都已完成）是纯粹的 summary 重建，不会打任何 SUT 请求。

**测试覆盖**：`test_runner_mock.py`（基线）、`test_runner_resume.py`（4 种续跑场景）、`test_runner_dual_judge.py`（3 种 run 级 κ 场景）。

### 2. Judge 引擎

**目录**：`services/eval-engine/src/evalops/judge/`

五种 judge kind —— 全部上线：

#### Rule judge（`rule.py` + `metrics.py`）— ✅ Week 1

确定性、零成本，是任何 hybrid funnel 的第一层。按 `CaseKind` 分派：

- **RAG case** — `exact_match`、`substring_match`、`f1`（SQuAD 风格的 token F1）、`citation_recall`、`context_precision`（recall 的对偶，抓"检索太冗余"）、`faithfulness_lite`（不用 LLM 的 token-overlap 代理，抓明显幻觉），当 `rubric.expected_refusal` 时额外加 `unanswerable_handling`。
- **Agent case** — `final_em`、`final_f1`、`tool_selection`（按序匹配期望工具）、`plan_efficiency`（expected_len / predicted_len，封顶）、当 `rubric.inject_failure` 时加 `error_recovery`。
- **Chat case** — `exact_match`、`f1`，或者退化到 `non_empty` 兜底。

所有原语在 `metrics.py`，23 个单测盯行为。

#### LLM-as-a-Judge（`llm.py` + `prompts.py`）— ✅ Week 2

基于 **LiteLLM**，换 provider 就是换字符串。三个子种：

| Kind | 怎么工作 | 缓解机制 |
|---|---|---|
| `LLM_SINGLE` | 单模型，每个指标一个 rubric prompt（当前是 `rag/faithfulness`、`rag/answer_relevancy`），严格 JSON 输出，容忍 markdown fence 的 parser | `repeats` + 标准差阈值 → 超阈值置 `unstable=True` |
| `LLM_PAIRWISE` | 每个 case 调两次（SUT=A baseline=B，再 swap），SUT 视角的投票对齐 | 位置偏差塌陷：如果两次都投位置 A 赢，结果直接判 TIE + unstable |
| `LLM_DUAL` | 主 + 次两个 provider 分别打分；case 级结果是均值 + `llm/dual_bin_agreement ∈ {0, 0.5, 1.0}` | Run 级 **Cohen's κ** 在 `_summarize` 里跨所有 case 的原始分数对算一次——这就是我们的"无人工标注"一致性代理 |

`LiteLLMClient` 包装层懒加载 `litellm`，默认安装不会被拖进一堆重依赖。测试用 `StubClient` 返回预置响应——**所有测试都不打真 API**。设 `EVALOPS_LLM_JUDGE=stub` 也能切到确定性 stub 做离线 CI 冒烟。

**测试覆盖**：`test_judge_llm.py` —— 13 个测试覆盖 parse、self-consistency、pairwise swap 异议、dual-judge bin agreement、κ 原语。

#### Agent-as-a-Judge（`agent.py`）— ✅ Week 3

EvalOps 和主流 LLM-as-a-Judge 框架的**核心差异点**。把 agent 的完整 ReAct trace（每一步 thought / action / observation）喂给一个 GPT-4 级模型（通过 LiteLLM），让它输出**四个维度的独立评分**：

| 维度 | 抓的问题 |
|---|---|
| `plan_quality` | 任务拆解是否合理，有没有多余步骤 |
| `tool_selection` | 每一步选的工具是否正确、参数是否合理 |
| `reasoning_coherence` | thought 是否和前一步 observation 一致 |
| `error_recovery` | 工具失败 / 返回空时，agent 有没有重试或换工具 |

- 每个维度发一条 `MetricScore`，再额外一条 `agent_judge/overall` 加权均值。`rubric.dimension_weights` 可以按维度加权。
- prompt（`prompts.py:AGENT_TRACE_USER`）对每个维度给出明确的 1.0 / 0.7 / 0.4 / 0.0 锚点示例——锚点确实能显著降低打分方差。
- trace 渲染时会把超长 observation 剪到 280 字符，整条 trace 封顶 40 步，避免 `file_read` case 用原始文件内容撑爆 token 预算。
- 每维自洽性校验：当 `repeats > 1` 时，任一维度的 stddev 超过 `rubric.unstable_stddev`（默认 0.15）就把整个结果标成 `unstable=True`。
- 解析错误永远不抛：一旦 judge 返回脏内容，四维全部退化成 0.0 + `unstable=True`。
- **测试覆盖**：`test_judge_agent.py` —— 6 个测试覆盖 happy path、trace 截断、自洽性、加权 overall、解析失败、空 trace，全部用 `StubClient`（**零真实 API 调用**）。

#### Hybrid judge（`hybrid.py`）— ✅ Week 3

三级 cost/quality 级联：`rule → LLM → Agent-as-a-Judge`。Rule 指标永远会先跑一遍而且**不会被丢掉**——Release Gate（Week 4）只信任 rule 层指标做 pass/fail 闸门，因为它确定；LLM/Agent 层并行跑出来做诊断。

- **升级策略**在 `_needs_llm` / `_needs_agent`。默认：RAG/CHAT case 在 `rag/faithfulness_lite < 0.7` 或 `rag/citation_recall < 0.5` 时升级到 LLM 层；`CaseKind.AGENT` case 一定触发 Agent-as-a-Judge。可以在 rubric 里覆写：`always_llm`、`skip_llm`、`always_agent_judge`、`skip_agent_judge`、`escalate_faithfulness`、`escalate_citation_recall`、`llm_model`、`agent_judge_model`。
- `judge_trace["escalations"]` 是实际触发的层的有序列表 —— `["rule"]`、`["rule","llm"]`、`["rule","agent"]` 或 `["rule","llm","agent"]`。Grafana 的"按层分解 judge 成本"面板就读这个。
- **懒构造**：LLM/Agent judge 只在第一条需要升级的 case 上才实例化，并按模型名缓存。只走 rule 的 run 连 LiteLLM import 都不触发。
- 响应 `EVALOPS_LLM_JUDGE=stub` 环境变量 —— `make smoke` / CI 不带真 API key 也能端到端跑通整条漏斗。
- **测试覆盖**：`test_judge_hybrid.py` —— 5 个测试覆盖 rule-only 短路、RAG 低 faithfulness 触发 LLM、agent case 强制触发 agent judge、`skip_*` 标志、`always_llm`。

### 3. SUT Adapter 与 Agent sidecar

**目录**：`services/eval-engine/src/evalops/adapters/`

每个 SUT 接入都实现一个 async 方法：`call(case, metadata) -> SutOutput`。目前三个 adapter：

- **`MockAdapter`** —— 纯内存、确定性，尊重 `rubric.mock_mode`（`faithful` / `hallucinate` / `refuse`），让单测可以**确定性复现特定失败模式**而无需任何外部服务。
- **`ReferenceAdapter`** —— `httpx.AsyncClient`，鉴权可选（预铸 token / user+password 登录 / 匿名），每次调用自动注入 `X-Request-ID` / `X-EvalOps-Run-Id` / `X-EvalOps-Case-Id` 头。`CaseKind.RAG` → `/api/v1/knowledge/query`，`CHAT` → `/api/v1/chat/sync`，`AGENT` → `/api/v1/agent/run`。
- **Agent sidecar** —— `sut-extensions/reference-agent-sidecar/`。因为参考应用本身没有 Agent 接口，我们新增了一个**纯增量的** FastAPI 服务：ReAct executor + 4 个锁定工具（`rag_query`、`calc`、`file_read`、`mock_web_search`），每个都有确定性失败注入开关（env var）。源码的 canonical 位置在 EvalOps 仓库，由 `scripts/deploy-sidecar.sh` 镜像到 companion app 树里跑。

### 4. 数据集

**目录**：`datasets/` + `services/eval-engine/src/evalops/datasets/`

四个数据集入库：

| 名称 | 规模 | 用途 |
|---|---|---|
| `rag-toy` | 4 case | 手工 rubric 驱动的冒烟 case，一秒内跑完、**每个** rule-judge 指标都能被触发。正常路径 / 幻觉 / 无答案 / 引用召回。 |
| `agent-toy` | 3 case | 工具选择、plan 效率、错误恢复三种 rubric。 |
| `hotpotqa-dev-100` | 100 case（约 1.4 MB） | 真实公开基准。HotpotQA dev-distractor split 前 100 题，由 `scripts/fetch-hotpotqa.sh` 确定性切片。100% 硬多跳，79 bridge + 21 comparison。 |
| `tau-bench-lite` | 20 case | Week 3 新增。参照 τ-bench / ToolBench 的 agent 基准，6 个子集：单步查找、多跳、多工具、file_read、unanswerable、error_recovery。每个 case 同时带 `input.preset_plan`（让 sidecar 的 ReAct executor 每次跑出**完全一致**的 trace，保证 Agent-as-a-Judge 分数稳定到能进 release gate）**和** `expected.trace`（让 MockAdapter 能离线 replay）。两份数据在构造时就对齐。 |

**Loader**（`datasets/__init__.py`）读 `benchmark.yaml` + `cases.yaml` 或 `cases/*.yaml`，解析成 Pydantic `Case` 对象，自动把字段转成 `CaseKind` / `CapabilityTag`。

**HotpotQA 适配器**（`datasets/hotpotqa.py`）把一条 HotpotQA 记录映射成一个 EvalOps `Case`：问题 → `input.query`，gold answer，supporting titles → `source_ids`，完整的 distractor context 展平到 `sources`（Week 3 的 LLM faithfulness judge 不需要真·检索器就有 ground truth），gold 支持句保留在 `expected.supporting_sentences`，`level` → `difficulty`（1/3/5），capability tag `rag/multi_hop`、`rag/level/<lvl>`、`rag/<type>`。

### 5. 可观测与可视化

**以下所有内容都是本地运行**（`make infra-up`）。**没有**远程或云端部署。

#### 结构化日志 — ✅

- `services/eval-engine/src/evalops/logging.py`：structlog 带自定义 `_inject_context` processor，从 `ContextVar` 读 `run_id` / `case_id` / `request_id` 并自动合并到**每一行**日志。
- `bind_run()` / `bind_case()` / `bind_request()` 每个 task 调一次；之后同一个 async task 里任何 `log.info(...)` 自动带上这些字段，零手工穿参。
- 两种输出模式：dev 的彩色控制台，以及 `EVALOPS_LOG_JSON=true` 的 JSON。
- Go 控制平面用 `zerolog`，用同样的 `request_id` 约定。

#### Prometheus 指标 — ✅ 控制平面 + eval-engine

**Go 控制平面** —— `services/control-plane/internal/observability/metrics.go`，端点 `GET /metrics`，端口 `:8090`：

| 指标 | 类型 | 标签 | 含义 |
|---|---|---|---|
| `evalops_cp_http_requests_total` | counter | `method`, `path`, `status` | HTTP 层流量（`Metrics()` 中间件） |
| `evalops_cp_http_request_duration_seconds` | histogram | `method`, `path` | 请求延迟（DefBuckets；`path` 用 `c.FullPath()` 防 label 爆炸） |
| `evalops_runs_submitted_total` | counter | `benchmark`, `sut` | 域：通过 `POST /api/v1/runs` 提交的 run 数 |
| `evalops_run_duration_seconds` | histogram | `benchmark`, `sut` | 域：run 墙钟耗时（scheduler 接线延后到 Week 4） |
| `evalops_run_cost_micro_usd_total` | counter | `benchmark`, `sut` | 域：judge + SUT 累计成本（scheduler 接线延后到 Week 4） |
| `evalops_run_pass_rate` | gauge | `benchmark`, `sut` | 域：最新完成 run 的通过率（scheduler 接线延后到 Week 4） |

Registry 是**进程私有**的（不是 default），Go runtime 噪音 opt-in —— 我们显式注册了 `ProcessCollector` + `GoCollector` 保持跟 default 对齐。

**Python 评测引擎** —— `services/eval-engine/src/evalops/observability/metrics.py` —— 同样用**进程私有** `CollectorRegistry`，前缀 `evalops_ee_*`，与 Go 侧在同一份 Grafana 面板上共存而不冲突标签。HTTP exporter **按需**启用：传 `--metrics-port 9100`（或设 `EVALOPS_PROMETHEUS_PORT=9100`）就会启动 HTTP server 跟着 run 的生命周期。CLI 冒烟完全不开 server。Prometheus 的抓取配置已经指向 `host.docker.internal:9100`。

| 指标 | 类型 | 标签 | 含义 |
|---|---|---|---|
| `evalops_ee_runs_total` | counter | `benchmark`, `sut`, `status` | Run 生命周期 —— 启动时发 `started`，终态时再发一次 |
| `evalops_ee_judge_calls_total` | counter | `kind`, `model` | 按 judge 层 / provider 计的调用次数 |
| `evalops_ee_judge_cost_micro_usd_total` | counter | `kind`, `model` | 累计 judge 成本，按层 / provider 归属 |
| `evalops_ee_run_duration_seconds` | histogram | `benchmark`, `sut` | Run 墙钟耗时；bucket 和 Go 侧一致以便同图 |
| `evalops_ee_case_duration_seconds` | histogram | `benchmark`, `sut`, `kind` | 单 case 耗时，按 `CaseKind` 分层 |
| `evalops_ee_run_pass_rate` | gauge | `benchmark`, `sut` | 最近一次 run 的 pass rate（每次 run 覆写） |
| `evalops_ee_run_judge_agreement` | gauge | `benchmark`, `sut` | 双 judge Cohen's κ，或 `-1` 表示 N/A |

Grafana 总览面板新增了 4 个读这些系列的 panel：eval-engine 按状态的 runs/sec、按层的 judge 成本燃烧、双 judge κ gauge、按 kind 的 case 耗时 p95。

#### 分布式追踪关联 — ✅ 头部 + exporter

- `ReferenceAdapter` 每次调用注入三个关联头：`X-Request-ID`（每个请求唯一）、`X-EvalOps-Run-Id`、`X-EvalOps-Case-Id`。
- Agent sidecar 在响应体里把它们原样回显，测试可以断言端到端传递。
- **Week 3**：eval-engine 侧的 OpenTelemetry span 已接通（`services/eval-engine/src/evalops/observability/tracing.py`）。`RunnerEngine.run` 在最外层套一个 `run_span`，每个 case 套一个子 `case_span`，属性与 Prometheus 的 label 集一致（`evalops.run_id`、`evalops.case_id`、`evalops.benchmark`、`evalops.sut`、`evalops.case_kind`）。当 `EVALOPS_OTEL_EXPORTER_ENDPOINT` 为空时 OTel 默认 no-op tracer 让整块代码零开销。把环境变量设成你的 OTLP HTTP endpoint（Jaeger 默认 `http://localhost:4328/v1/traces`），`TracerProvider` + `OTLPSpanExporter` 就会由 CLI 里的 `configure_tracing()` 懒加载起来。

#### Prometheus 抓取 — ✅

`infra/prometheus/prometheus.yml` —— Prometheus 跑在 docker 里，`scrape_interval: 15s`：

```yaml
  - job_name: evalops-control-plane
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:8090"]
        labels: {service: control-plane, env: local}

  - job_name: evalops-eval-engine
    metrics_path: /metrics
    static_configs:
      - targets: ["host.docker.internal:9100"]
        labels: {service: eval-engine, env: local}
```

`host.docker.internal` 是 macOS/Windows Docker Desktop 指向宿主机的别名——这就是容器内 Prometheus 去抓宿主机上 Go / Python 进程的惯用法，不用折腾 bridge network。

#### Grafana 面板 — ✅

通过 provisioning 自动加载（`infra/grafana/provisioning/`）：

- **数据源**：Prometheus（http://prometheus:9090）+ Jaeger（http://jaeger:16686），都走 docker-compose 服务名
- **面板**：从 `infra/grafana/dashboards/*.json` 自动加载，一文件一面板

自带 **"EvalOps Overview"** —— 5 个 panel：

| Panel | PromQL |
|---|---|
| 每秒提交 run 数 | `sum(rate(evalops_runs_submitted_total[5m])) by (benchmark, sut)` |
| 最新通过率 | `evalops_run_pass_rate` |
| 成本烧速（µUSD/hr） | `sum(rate(evalops_run_cost_micro_usd_total[1h])) by (benchmark, sut)` |
| 控制平面 HTTP RPS | `sum(rate(evalops_cp_http_requests_total[1m])) by (path, status)` |
| 控制平面 HTTP p95 延迟 | `histogram_quantile(0.95, sum(rate(evalops_cp_http_request_duration_seconds_bucket[5m])) by (le, path))` |

访问 `http://localhost:3001`（admin / admin），EvalOps 文件夹已预建。

#### Jaeger — ✅ 跑着，⏳ 未接收

`jaegertracing/all-in-one` 容器，`COLLECTOR_OTLP_ENABLED=true`。端口：UI `:16696`，OTLP gRPC `:4327`，OTLP HTTP `:4328`。设 `EVALOPS_OTEL_EXPORTER_ENDPOINT=http://localhost:4328/v1/traces` 后 eval-engine 就会把 `run_span` / `case_span` 推到这里。

#### CLI 运行报告 — ✅

`evalops report <run.json>` 用 `rich.Table` 渲染：

1. **Summary** —— run ID、benchmark + 版本、SUT、状态、通过率、unstable 数、总成本（µUSD）、prompt + completion token
2. **指标平均** —— 任何 case 出现过的指标，取平均
3. **逐 case 表格** —— case id、pass/fail、延迟、头条指标、错误信息

当 docker 栈没起的时候，这就是单次观察的界面。快、零依赖（`rich` 已经是 Typer 的运行时依赖）。

#### Web 前端 — ✅ 占位

`web/frontend/` —— Vite + React 18 + TypeScript + Ant Design 5，跑在 `:5180`，`/api → :8090` 代理。当前只有一页信息页展示四大支柱和 per-week 状态标签。真正的 run-list / 雷达 / case-diff dashboard 在 Week 4 交付。

**已在浏览器端到端验证** —— 通过 `preview_start name=evalops-web` 验证：console 空、server log 空、所有 antd 组件正常渲染。

### 6. Proto 契约与代码生成

**目录**：`proto/evalops/v1/`

四个 `.proto` 文件：

| 文件 | 定义什么 |
|---|---|
| `common.proto` | `Metadata`（request_id/trace_id/run_id/case_id）、`Cost`（micro-USD + token）、`KV`、`CapabilityTag` |
| `dataset.proto` | `DatasetService`（CreateBenchmark / AddCases / ListCases）、`Benchmark`、`Case`（per-kind schema 用 JSON 嵌在 proto 里）、`CaseKind` 枚举 |
| `judge.proto` | `JudgeService`（Score / ScoreBatch）、`JudgeConfig` 带**内容寻址的 hash**做缓存键、`JudgeKind` 包含一等公民 `LLM_DUAL` |
| `runner.proto` | `RunnerService`（SubmitRun / StreamRunEvents / CancelRun）、`Run`、`RunSummary`、`RunEvent` oneof union 用于流式推送（Heartbeat / CaseCompleted / RunFinished / RunFailed） |

**为什么 case/rubric 负载用 JSON-in-proto**：`CaseKind` 在 RAG / Agent / Hybrid 下 schema 差别很大。用 `oneof` 编码会在每新增任务类型时炸 .proto。JSON blob 让线上契约保持紧凑，per-kind schema 在代码里演进。

**为什么 `JudgeConfig` 做内容寻址**：两个 judge name 不同但 rubric/model/temperature 相同的 run 应该可比；两个看起来一样但 rubric 不同的必须不可比。对规范化后的 config 做 hash 就成了 judge 缓存键。

**代码生成** —— `make proto-gen`：
- 用 **buf** 配远程插件（`buf.build/protocolbuffers/go`、`grpc/go`、`protocolbuffers/python`、`grpc/python`）→ **本机不用装 `protoc`**
- Go 输出 → `services/control-plane/internal/proto/evalops/v1/`（托管的 `go_package_prefix`）
- Python 输出 → `services/eval-engine/src/evalops/v1/`（这个路径特意选的，让 `from evalops.v1 import common_pb2` 原生可达，不需要 `protoletariat`）
- 生成的 stub **入 git**，这样 `go build` 和 `pytest` 都不需要 buf
- `make proto-check` 重新生成并与 tree diff；CI 每次 PR 都跑

### 7. CI 护栏

**文件**：`.github/workflows/ci.yml`

六条并行流水线，`concurrency: ci-<ref>` 让同一分支的旧 run 被新 push 自动取消：

| 流水线 | 跑什么 |
|---|---|
| `eval-engine` | Python 3.11 **和** 3.12 矩阵 · `pip install -e '.[dev]'` · `ruff check` · `pytest -q --cov=evalops` |
| `control-plane` | Go 1.23 · `go vet ./...` · `go build ./...` · `go test -race ./...` |
| `agent-sidecar` | `pip install -e .` · 导入冒烟（`from agent_sidecar.server import create_app; create_app()`） |
| `proto` | `buf lint` · `buf generate` · 如果提交的 stub 与生成结果有漂移则 fail |
| `sidecar-sync` | 结构性检查 `sut-extensions/` 和 `scripts/deploy-sidecar.sh` |
| `web` | Node 22 · `npm ci` · `npm run typecheck` · `npm run build` |

---

## 仓库布局

```
evalops/
├── proto/
│   ├── evalops/v1/                    # 线上契约
│   ├── buf.yaml                       # Lint 规则（对内部 API 放宽 RPC_*_UNIQUE）
│   └── buf.gen.yaml                   # 远程插件配置
├── services/
│   ├── control-plane/                 # Go · Gin · zerolog · Prometheus
│   │   ├── cmd/server/                # 二进制入口
│   │   ├── internal/
│   │   │   ├── config/                # env-var 配置
│   │   │   ├── handler/               # Gin handler（health、runs stub）
│   │   │   ├── middleware/            # request_id、logger、metrics
│   │   │   ├── observability/         # Prometheus registry + 指标声明
│   │   │   ├── router/                # 路由接线
│   │   │   └── proto/                 # buf 生成的 stub（入库）
│   └── eval-engine/                   # Python · Pydantic · anyio · structlog · LiteLLM
│       ├── src/evalops/
│       │   ├── cli/main.py            # Typer CLI：run / report / show-benchmark
│       │   ├── runner/engine.py       # RunnerEngine + resume 逻辑
│       │   ├── judge/                 # base / rule / llm / llm_stub / metrics / prompts
│       │   ├── adapters/              # base / mock / reference
│       │   ├── datasets/              # loader + hotpotqa 适配器
│       │   ├── v1/                    # buf 生成的 stub（入库）
│       │   ├── config.py              # pydantic-settings
│       │   ├── logging.py             # structlog + contextvars
│       │   └── models.py              # 17 个 Pydantic DTO（proto 的进程内对应）
│       └── tests/                     # 44 个通过 —— metrics、runner、llm judge、resume、dual
├── sut-extensions/
│   └── reference-agent-sidecar/       # reference-app 增量补丁的版本控制源
│       └── src/agent_sidecar/         # FastAPI · ReAct executor · 4 工具 + 失败注入
├── datasets/
│   ├── rag-toy/                       # 4 个手工 RAG case
│   ├── agent-toy/                     # 3 个手工 Agent case
│   └── hotpotqa-dev-100/              # 100 个公开多跳 case
├── infra/
│   ├── docker-compose.yml             # PG + Redis + MinIO + Jaeger + Prometheus + Grafana
│   ├── prometheus/prometheus.yml
│   └── grafana/
│       ├── provisioning/              # 数据源 + 面板自动加载
│       └── dashboards/                # EvalOps Overview JSON
├── web/frontend/                      # Vite + React + TS + antd 占位
├── docs/                              # 架构、每周状态、reference SUT 增量清单
│   ├── architecture.md
│   ├── week1-status.md
│   ├── week2-status.md
│   └── reference-sut-changeset.md
├── scripts/
│   ├── deploy-sidecar.sh              # 把 sut-extensions 镜像到 reference-app tree
│   └── fetch-hotpotqa.sh              # 确定性切片（size/split 可配）
├── .github/workflows/ci.yml           # 6 条并行流
└── Makefile                           # infra-up / proto-gen / smoke / sidecar-* / ...
```

---

## 快速开始

### 1. 环境

永远用专用 conda env —— **不要用 base**：

```bash
conda create -n evalops python=3.12 -y
conda activate evalops
pip install -e 'services/eval-engine[dev]'            # 核心 + 测试 + ruff
pip install -e 'services/eval-engine[llm-judge]'      # LiteLLM（可选）
pip install -e sut-extensions/reference-agent-sidecar
```

### 2. 拉起可观测栈（可选）

```bash
make infra-up      # PG / Redis / MinIO / Jaeger / Prometheus / Grafana
make infra-ps      # 健康检查
```

然后打开：

- **Grafana**：http://localhost:3001（admin / admin）→ EvalOps Overview
- **Prometheus**：http://localhost:9091
- **Jaeger**：http://localhost:16696

### 3. 跑基准

```bash
# 玩具 RAG 冒烟（不需要任何外部服务）
evalops run --benchmark datasets/rag-toy --sut mock --out runs/toy.json

# HotpotQA dev-100 打 mock SUT
evalops run --benchmark datasets/hotpotqa-dev-100 --sut mock --out runs/hotpot.json

# 通过 reference-app agent sidecar 跑 Agent 基准（真·SUT 进程）
scripts/deploy-sidecar.sh --reinstall
AGENT_SIDECAR_PORT=18081 agent-sidecar &
evalops run --benchmark datasets/agent-toy \
            --sut reference \
            --sut-endpoint http://localhost:18081 \
            --out runs/agent.json

# LLM-as-a-Judge（需要 API key）
export OPENAI_API_KEY=...
evalops run --benchmark datasets/hotpotqa-dev-100 --sut mock \
            --judge llm_single --judge-name gpt4o \
            --out runs/hotpot-llm.json

# Dual-judge 带 run 级 Cohen's κ（两个 provider）
export OPENAI_API_KEY=... ANTHROPIC_API_KEY=...
evalops run --benchmark datasets/rag-toy --sut mock \
            --judge llm_dual --judge-name gpt4o-vs-claude \
            --out runs/dual.json

# 离线 stub LLM judge（CI 友好，不需 API key）
EVALOPS_LLM_JUDGE=stub evalops run --benchmark datasets/rag-toy \
            --sut mock --judge llm_single --out runs/stub.json

# 断点续跑 —— 保留原 run_id
evalops run --benchmark datasets/rag-toy --sut mock \
            --resume runs/toy.json --out runs/toy-resumed.json

# 查看任意 run
evalops report runs/toy.json
```

### 4. 启动 web 占位

```bash
cd web/frontend
npm ci
npm run dev    # http://localhost:5180
```

### 5. 启动 Go 控制平面

```bash
cd services/control-plane
go run ./cmd/server    # http://localhost:8090
# /healthz · /readyz · /metrics · POST /api/v1/runs (stub) · GET /api/v1/runs/:id
```

---

## CLI 参考

```
evalops run         跑一个基准打 SUT，写一份 Run JSON 报告
  --benchmark       benchmark 目录路径（必须包含 benchmark.yaml）
  --sut             mock | reference
  --sut-endpoint    覆盖端点 URL（例如 sidecar 的 http://localhost:18081）
  --judge           rule | llm_single | llm_pairwise | llm_dual | hybrid
  --judge-name      run 的自定义名字
  --concurrency     SUT 并发调用数（默认 4）
  --max-cases       只跑前 N 个 case（0 = 全部）
  --out             Run JSON 输出路径（默认 runs/latest.json）
  --resume          读一份先前的 Run JSON，已完成的 case 直接继承
  --log-level       INFO | DEBUG | WARNING | ERROR

evalops report      漂亮地打印已有的 Run JSON
  <path>            run JSON 的路径

evalops show-benchmark    dump 基准的元数据 + case 数
  <path>            benchmark 目录的路径
```

---

## 本地端口地图

所有服务都通过 `make infra-up` 本地运行 —— **没有任何远程基础设施**。端口刻意避开 companion app 的默认值，这样两套栈可以同时跑。

| 组件 | 宿主端口 | URL | 备注 |
|---|---|---|---|
| Go 控制平面 | 8090 | http://localhost:8090 | `/healthz`、`/metrics`、`/api/v1/runs` |
| Python eval-engine 指标 | 9100 | — | 默认 disabled（`EVALOPS_PROMETHEUS_PORT=0`） |
| Web 前端（Vite） | 5180 | http://localhost:5180 | `/api` 代理到 :8090 |
| PostgreSQL | 5452 | `postgres://evalops:evalops@localhost:5452/evalops` | |
| Redis | 6389 | `redis://localhost:6389` | |
| MinIO S3 API | 9010 | http://localhost:9010 | `evalops` / `${EVALOPS_MINIO_ROOT_PASSWORD}` |
| MinIO 控制台 | 9011 | http://localhost:9011 | |
| Jaeger UI | 16696 | http://localhost:16696 | 设 `EVALOPS_OTEL_EXPORTER_ENDPOINT=http://localhost:4328/v1/traces` 后即可接收 eval-engine 的 span |
| Jaeger OTLP gRPC | 4327 | — | 就绪接收 |
| Jaeger OTLP HTTP | 4328 | — | 就绪接收 |
| Prometheus | 9091 | http://localhost:9091 | |
| Grafana | 3001 | http://localhost:3001 | admin / admin；"EvalOps Overview" 自动加载 |
| Agent sidecar | 18081 | http://localhost:18081 | 手动起：`AGENT_SIDECAR_PORT=18081 agent-sidecar` |

---

## 交付状态

| 周 | 已交付内容 | 细节 |
|---|---|---|
| **Week 1** ✅ | Mono-repo 骨架、rule judge、mock + HTTP adapter、agent sidecar、本地 docker-compose、16 测试 | [`docs/week1-status.md`](docs/week1-status.md) |
| **Week 2** ✅ | buf 链路、CI 六条线、web 占位、LiteLLM judge、HotpotQA-dev-100、深度 RAG 指标、runner resume、44 测试 | [`docs/week2-status.md`](docs/week2-status.md) |
| **Week 3** ✅ | Agent-as-a-Judge（4 维）、rule→LLM→agent 三级 hybrid funnel、τ-bench-lite（20 case）、Python OTel span + 进程私有 Prometheus registry（7 个指标族）、Grafana 新增 4 个面板、64 个测试 | [`docs/week3-status.md`](docs/week3-status.md) |
| **Week 4** ⏳ | Bad-case harvester、Release Gate CI、能力雷达 Grafana 面板、真实 web dashboard、ClickHouse OLAP、hybrid 按层拆解成本归属 | — |

完整规划：[`../.claude/plans/eager-zooming-rabin.md`](../.claude/plans/eager-zooming-rabin.md)。

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
