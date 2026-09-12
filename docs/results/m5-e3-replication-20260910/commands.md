# 执行命令与目录别名

实际argv、模块路径、stdout/stderr和逐attempt记录已同步保存在原研究服务器。
以下使用可迁移的目录别名，不发布账号或本机绝对路径。

- `PCDB_PYTHON`：既有解释器，NumPy2.4.6、scikit-learn1.9.0、xgboost-cpu3.2.0。
- `PCDB_C_CACHE`：既有良性C缓存根，含data-001、原运行、model-diagnosis-001。
- `PCDB_E3_CACHE`：原E3研究根，含full-001、frontier-full-001及其private预测/特征。
- `PCDB_REPL_OUTPUT`：本次输出根；子目录必须不存在。实际attempt名称为
  replication-smoke-001和replication-full-001。

在本PR代码根执行；所需旧helper/runtime/输入/预测身份由脚本逐项检查，不能以其他版本替代。

```sh
PYTHONPATH=src:scripts "$PCDB_PYTHON" scripts/run_m5_e3_replication.py --self-test
PYTHONPATH=src:scripts "$PCDB_PYTHON" scripts/run_m5_e3_replication.py \
  --study "$PCDB_C_CACHE" --previous "$PCDB_E3_CACHE" \
  --output "$PCDB_REPL_OUTPUT/replication-smoke-001" \
  --plan docs/experiments/M5_E3_REPLICATION.md --mode smoke --wall-seconds 90
PYTHONPATH=src:scripts "$PCDB_PYTHON" scripts/run_m5_e3_replication.py \
  --study "$PCDB_C_CACHE" --previous "$PCDB_E3_CACHE" \
  --output "$PCDB_REPL_OUTPUT/replication-full-001" \
  --plan docs/experiments/M5_E3_REPLICATION.md --mode full --wall-seconds 800
```

必须先核对smoke成功、旧两臂预测精确重建和剩余累计预算再运行full。
本轮实际smoke/full均首次成功；原命令使用身份等同的服务器源文件布局。

以下聚合验证命令已实际在本地执行，仅需现有Python/NumPy和PR内紧凑产物。
复算时使用新的输出文件名，不覆盖已发布verification.json。

```sh
python3 -B scripts/summarize_m5_e3_replication.py \
  --full docs/results/m5-e3-replication-20260910/full \
  --smoke docs/results/m5-e3-replication-20260910/smoke \
  --output /tmp/m5-e3-replication-verification-new.json
```
