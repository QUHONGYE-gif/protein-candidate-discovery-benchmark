# Protein Candidate Discovery Benchmark

面向公开蛋白数据的小样本高价值候选发现 Benchmark，重点研究固定标签预算或固定总成本下，
不同 surrogate model、uncertainty method 与 acquisition strategy 的实际候选发现价值。

截至 2026-08-13，项目已完成 **Alpha＋IRED 首批 M2 静态基线闭环**，并在 Issue #68 完成
M3 deterministic single-fidelity replay 的正式执行、162-object immutable COS publication、独立
fresh readback 与 Gate 2 engineering evidence 终审。M0 的 repository/contracts implementation 与
CI 已通过 PR #2 进入 `main`；整个 M1 dataset portfolio 仍按 task/assay 逐项推进，M4、held-out、
external validation 与最终 scientific validation 尚未开始或授权。

## 研究问题

- 预测指标更好的 surrogate model 是否一定具有更好的 decision value？
- 不同 initial sample size、high-value sparsity 和 noise 条件下应选择什么策略？
- 低保真信息在什么 correlation/cost 区间能够减少高保真标签需求？
- 方法在 distribution shift 与 cross-complex split 下能否保持可靠？

## 系统边界

- GitHub：代码、配置、Schema、Issue、PR 和小型公开 fixture；
- COS：公开原始数据、处理数据、embedding、trajectory、日志和冻结结果；
- Compute server：通过精确 Git commit 或 container digest 执行实验；
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
manifests/     versioned compact manifest copies; large objects remain in COS
schemas/       dataset, processing, split, experiment, RunSpec and attempt contracts
src/pcdb/      benchmark implementation
tests/         unit, contract, leakage and integration tests
docs/          科学协议、数据边界、复现说明和 ADR
data/          仅保留存储政策；实际数据不进入 Git
results/       仅保留产物政策；正式结果写入 COS
```

开发前请阅读 [AGENTS.md](AGENTS.md)、[CONTEXT.md](CONTEXT.md)、
[Benchmark Contract](docs/BENCHMARK_CONTRACT.md) 和
[Privacy Boundary](docs/PRIVACY_BOUNDARY.md)。

项目的长期研究定位、范围控制和阶段路线分别见
[Project Background](docs/PROJECT_BACKGROUND.md)、
[Project Direction](docs/PROJECT_DIRECTION.md) 和
[Execution Roadmap](docs/EXECUTION_ROADMAP.md)。

## 当前状态

- M0 engineering readiness：repository、contracts 与 CI 基线已进入 `main`；
- M1 dataset onboarding：按 task/assay 进行中；Alpha Amylase＋IRED 已由 Issue #42 获得首批
  M2 entry cohort 的 Gate 1 PASS，但整个 M1 portfolio 仍未完成；
- M2 static-baseline closure：production Experiment Config v2、feature/prediction artifact、canonical
  feature identity 与 Ridge、Random Forest、XGBoost producer 均已进入 `main`；Issue #58 已在
  Alpha＋IRED、seed `1729` 的冻结 `2 tasks × 3 models` 范围内完成正式 server execution、
  68-object immutable COS publication、独立 fresh readback 与 final evidence review；该结论只限
  两个 task 的 static-baseline evidence，不支持跨 task 普遍最优或最终 scientific validation；
- M3 deterministic replay closure：Issues #66/#67 已完成 protocol freeze 与 synthetic TDD；Issue
  #68 随后在冻结的 Alpha＋IRED、`2 tasks × 3 models × 3 acquisitions × seed 1729` 范围内完成
  Gate A real-input preflight，以及一次无 retry/replacement 的四阶段 Gate B sequence。18 个
  RunSpecs 的 162 个对象完成 forbid-overwrite publication 与 independent final readback，最终独立
  evidence review 判定五项 Gate 2 engineering conditions `5/5 PASS`；该结果不支持模型/策略科学
  优越性，也不授权 M4、held-out 或 external validation；
- Raw-ingest runtime：按 task 独立记录，不替代 processing、split 或 scientific validation；
- Scientific validation：尚未开始；
- Held-out evaluation：未授权；
- COS credential availability：Issues #58/#68 的窄权限 CVM Roles 已完成各自正式
  execution/readback；这些历史成功不证明未来 M4 或其他操作仍具备凭据、权限或执行授权。

## 协作

工作通过 GitHub Issue、短期 branch 和 reviewed PR 推进。贡献规则见
[CONTRIBUTING.md](CONTRIBUTING.md)。
