# Issue #28：high-value Experiment Config v2 设计证据

> 状态：**非生产 proposal**。本目录不构成 accepted Schema、Experiment Config、RunSpec、
> evaluator 实现、Gate 1 PASS 或 M2 授权。

## 目的

本目录保存 Issue #28 最小信息实验缺失的可复现证据，并验证一个设计假设：

> 如果完整 high-value evaluation semantics 全部由 Experiment Config v2 拥有，而 RunSpec 继续只
> 绑定完整 Experiment Config 的 SHA-256，是否可以在不改变 ADR-0003 职责边界的前提下持久化
> high-value 契约。

这里的 Alpha Amylase 与 IRED 上尾 1%及 0.5%/2% sensitivity 仍然只是工作假设。文件不得用作正式
实验配置，也不得用来读取 candidate-pool、validation 或 held-out truth。

## 文件

- `experiment-v2.prototype.schema.json`：非生产 Schema v2 proposal；
- `alpha.prototype.json`、`ired.prototype.json`：绑定已接受 manifest chain 的非生产实例；
- `synthetic-fixtures.json`：尺度、quantile/Top-k、并列、非有限值和 task-first fixtures；
- `verify.py`：只依赖 Python 标准库和项目已有 `jsonschema` 的独立 verifier；
- `expected-results.json`：verifier 的冻结期望输出。

## 精确 manifest chain

| Task | Dataset Manifest SHA-256 | Processing Manifest SHA-256 | Split Manifest SHA-256 |
|---|---|---|---|
| Alpha Amylase | `c1679ca0d58c69f368b34974fbf43e9b23579892d0a7daef04bb1e354e1a1bac` | `cc0582a0c5f3a2d854c9c64c78b2995fd81d87e328241cd42121deee0ca2aaec` | `c6b67b962b78656f8083b088909d801dd8c40c908333f310a4fc39cb86b3184a` |
| IRED | `37938d62ec61c0ac912bfd49d690696d7b750f0a497558faf525a9b7d3e61643` | `929f0a46bfca015fb020bb7ebcc3a91e4de33922cd217399551b3dae1cc437ba` | `9bff199ec81b9b5b1e0548c237b923b2ac7db1bf36382377ed36d30c042c725e` |

这些 identities 来自已接受并正式发布的 Dataset、Processing 与 Split Manifest。proposal 同时冻结
candidate role 为 `candidate-pool`：Alpha 为 2,262 candidates，IRED 为 2,070 candidates。

## Canonicalization procedure

`verify.py` 对解析后的 JSON value 使用以下 reference procedure 生成 canonical semantic bytes：

```python
json.dumps(
    value,
    allow_nan=False,
    ensure_ascii=False,
    separators=(",", ":"),
    sort_keys=True,
).encode("utf-8")
```

规则是 UTF-8、key 按 Unicode code point 排序、无非必要空白、拒绝 NaN/Infinity、无尾随换行。
verifier 还分别报告 tracked exact file bytes 的 SHA-256，避免把 semantic hash 与文件 identity
混淆。生产实现前仍需明确决定 Experiment Config identity 使用 exact file bytes，还是把上述
canonical procedure 升格为公共契约。

## Prototype 表达的语义

- quantile 与固定 Top-k 是不同契约；
- absolute、WT-relative、quantile、固定 Top-k 与 near-optimal 使用不同条件字段；hybrid 由稳定的
  primary/sensitivity definitions 加连续指标共同表达；
- quantile 明确 fraction、`ceil`、frozen candidate count 与 nominal K；
- rank order 固定为 label descending、`candidate_id` ascending；
- K 边界同分全部纳入，非有限值和 duplicate candidate ID fail closed；
- primary 与 sensitivity 使用稳定 ID，sensitivity 不得替换 primary；
- evaluator/hidden oracle 拥有 high-value 计算，feature、model、calibration 与 acquisition 无权访问；
- M2 static metrics 与 M3–M5 sequential decision metrics 分开；
- 跨任务以 task-first 为 primary aggregation，seed 只估计算法随机性；
- nominal-K 命中概率不是实际阳性率、replay evidence 或冻结预算；
- 未读真实 tie expansion 只报告预注册的 `[nominal_k, frozen_candidate_count]` 上下界。

## 复现

从仓库根目录执行：

```bash
.venv/bin/python docs/proposals/issue-28-high-value-schema-v2/verify.py
```

成功时输出 `Issue #28 prototype verification: PASS`。`--emit-results` 会把重新计算的结果写到
stdout，便于人工比较；它不会修改任何文件。

## 设计结论与后续门禁

本 proposal 的成功只说明“Schema v2 可以成为完整契约的候选设计”。它不接受 Top 1%，也不代表
production Schema 应立即采用这些字段。

如果独立复核确认：

1. 所需语义均留在 Experiment Config；
2. RunSpec 继续只通过 `experiment_config_sha256` 绑定完整配置；
3. Dataset/Processing/Split、held-out 与运行身份职责未移动；

则 ADR-0003 无需 supersede。若后续设计要求 RunSpec 直接理解 high-value 子字段，或把 threshold/
metric/evaluation semantics 移出 Experiment Config，则必须暂停并重新审查 ADR。
