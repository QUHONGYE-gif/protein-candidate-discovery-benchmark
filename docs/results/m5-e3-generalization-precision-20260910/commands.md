# 复现命令与保留边界

执行身份、版本、输入/源码/便笺哈希见full/execution.json及subsets/execution.json。
真实argv、PYTHONPATH、stdout/stderr与候选级产物留在原批准服务器，下面用符号路径。
原数据、特征和成功结果只读；所有NEW目录及文件必须不存在。

服务器复用既有Python，NumPy2.4.6、scikit-learn1.9.0、xgboost-cpu3.2.0，无新安装。
新源码目录包含本runner、原replication和fixed_count_coverage helper；PYTHONPATH包含
原benign runtime的src/scripts以及原E3 source。BENIGN_STUDY为批准C3缓存，E3_STUDY
含原full-001特征与replication-full-001保存预测和元数据。

```sh
python scripts/run_m5_e3_generalization.py --self-test
python scripts/run_m5_e3_generalization.py --study BENIGN_STUDY --previous E3_STUDY --output NEW_SMOKE --plan docs/experiments/M5_E3_GENERALIZATION_PRECISION.md --mode smoke --wall-seconds 90
# 接通成功、XGB原点预测精确重建且累计预算足够后执行。
python scripts/run_m5_e3_generalization.py --study BENIGN_STUDY --previous E3_STUDY --output NEW_FULL --plan docs/experiments/M5_E3_GENERALIZATION_PRECISION.md --mode full --wall-seconds 800
```

两个实际attempt的服务器后缀均为001；全部记录见generalization-attempts.jsonl，无失败
或未报告的重试。单worker/线程、累计15分钟/1.5GiB/256MiB，紧凑返回上限5MiB。
每状态只向worker提供原允许32/64观测，先保存模型、预测和选择，worker退出再评分。

本地有限子集诊断与独立核验只需已有NumPy，不加载模型依赖或候选级真值：

```sh
OPENBLAS_NUM_THREADS=1 python3 scripts/analyze_m5_e3_state_subsets.py --self-test
OPENBLAS_NUM_THREADS=1 python3 scripts/analyze_m5_e3_state_subsets.py --output NEW_SUBSETS
OPENBLAS_NUM_THREADS=1 python3 scripts/verify_m5_e3_generalization_precision.py --directory docs/results/m5-e3-generalization-precision-20260910 --output NEW_VERIFICATION.json
```

分析枚举全部子集；核验器独立使用整数子集和动态规划，不复用枚举分布。子集分析首个
attempt完成后停止，范围为固定20个已见seed目录，不是新的随机总体抽样或population CI。
