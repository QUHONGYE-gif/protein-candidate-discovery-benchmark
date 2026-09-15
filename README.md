# Protein Candidate Discovery Benchmark

面向公开蛋白数据的小样本高价值候选发现 Benchmark，重点研究固定标签预算或固定总成本下，
不同 surrogate model、uncertainty method 与 acquisition strategy 的实际候选发现价值。

项目当前所处阶段、最新 accepted result、活跃 PR/实验、阻塞项与下一 promotion gate 统一见
[Current project status](docs/STATUS.md)。README 只作为稳定入口，不复制易漂移的运行状态。

研究结果补充见 [M6 E4 原因核查与 BLAT C0 评分消融](docs/results/m6-e4-diagnosis-20260915/README.md)，
包含完整消融终点、原 E4 紧凑诊断、曲线、来源与复核边界。该结果入口不替代里程碑 Gate。

## 研究问题

- 预测指标更好的 surrogate model 是否一定具有更好的 decision value？
- 不同 initial sample size、high-value sparsity 和 noise 条件下应选择什么策略？
- 低保真信息在什么 correlation/cost 区间能够减少高保真标签需求？
- 方法在 distribution shift 与 cross-complex split 下能否保持可靠？

## 系统边界

- GitHub：代码、配置、Schema、Issue、PR 和小型公开 fixture；
- Compute server：ADR-0005 合入后新授权的 Governance v2 milestone，以服务器作为 active
  raw/processed/embedding/split、普通 run 和 release candidate 事实基准；
- COS：保留 M0–M4 及已授权的 issue-scoped M5 历史对象；新 Governance v2 milestone 只在
  Gate B 后写入有效 terminal milestone 的精简、不可覆盖 archive，普通 run 零 COS write；
- Local NVMe：可清理的 cache/scratch，不作为最终证据存储。

项目只允许使用公开数据。内部序列、内部筛选结果、私有结构/MD/湿实验结果和内部阈值不得
进入代码库、COS 项目空间、Issue、PR 或 Benchmark 配置。

## 快速开始

要求 Python 3.11 或更高版本：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
make check
```

## M2 synthetic 开发入口

Issue #55/#56 的 `pcdb m2-synthetic` 入口只用于公开 synthetic、三角色的本地 scratch 检查。
当前冻结的 model IDs 为 `ridge-sklearn-v1`、`random-forest-sklearn-v1` 和 CPU-only、单线程的
`xgboost-cpu-v1`：

```bash
pcdb m2-synthetic \
  --roles roles.json \
  --development-observations development-observations.json \
  --experiment-config experiment.json \
  --model-id xgboost-cpu-v1 \
  --scratch-root .scratch/m2-synthetic
```

`roles.json` 必须显式提供 `benchmark_task_id`、`generator.git_commit`、
`generator.environment_digest`，以及 `development`、`validation`、`candidate-pool` 三组
`candidate_id,sequence`。observation 文件只能包含 `development` 数组及其中的
`candidate_id,observation`。入口没有 validation/candidate truth、held-out、evaluator、COS 或远程
调度参数；仓库内输出必须位于已被 Git 忽略的 `.scratch/` 命名空间，仓库外绝对路径仍可使用。
输出不是正式 RunAttempt、benchmark evidence 或 scientific result。

## 目录

```text
configs/       versioned experiment configuration
manifests/     versioned compact manifest copies; large objects use the version-appropriate data plane
schemas/       dataset, processing, split, experiment, RunSpec and attempt contracts
src/pcdb/      benchmark implementation
tests/         unit, contract, leakage and integration tests
docs/          科学协议、数据边界、复现说明和 ADR
data/          仅保留存储政策；实际数据不进入 Git，新 Governance v2 active data 位于服务器
results/       仅保留产物政策；Governance v2 terminal archive 在 Gate B 后写入 COS
```

开发前请阅读 [AGENTS.md](AGENTS.md)、[CONTEXT.md](CONTEXT.md)、
[Benchmark Contract](docs/BENCHMARK_CONTRACT.md) 和
[Privacy Boundary](docs/PRIVACY_BOUNDARY.md)。

新 milestone 还必须阅读 [Governance v2](docs/GOVERNANCE_V2.md)、
[server-first/archive runbook](docs/GOVERNANCE_V2_RUNBOOK.md) 和
[Legacy Execution Surface index](docs/LEGACY_EXECUTION_SURFACE.md)。本地与 CI 通过同一入口
`make governance-check` 执行 Governance v2 的静态机器检查；该 Check 不产生科学批准事件。

项目的实时状态、长期研究定位、范围控制和阶段路线分别见
[Current project status](docs/STATUS.md)、
[Project Background](docs/PROJECT_BACKGROUND.md)、
[Project Direction](docs/PROJECT_DIRECTION.md) 和
[Execution Roadmap](docs/EXECUTION_ROADMAP.md)。M4 之后的规范路线以
[M4 to M7 revised experiment plan](docs/M4_TO_M7_REVISED_EXPERIMENT_PLAN.md) 为准；旧路线图保留为
历史规划基线。

## 当前状态

当前事实只在 [docs/STATUS.md](docs/STATUS.md) 维护。仅在 Gate A scientific transition、
Gate B terminal 或 milestone closeout 时更新该文件；普通 PR/CI、run/retry、日志、plot 和
maintenance batch 不触发更新。不在 README、历史路线图和多个 Issue 评论中复制另一份
“当前状态”。

## 协作

所有进入 `main` 的工作通过短期 branch 和 reviewed PR 推进；达到独立 tracking 门槛时，从
GitHub Issue 或 Multica 中选择一个权威来源。选择 GitHub 时，多个 Multica execution issues
可以共用一个 GitHub 父 Issue；选择 Multica 时不创建形式性的 GitHub parent。PR 分类、
maintenance batch 触发点与 authority 字段见 [CONTRIBUTING.md](CONTRIBUTING.md)。
