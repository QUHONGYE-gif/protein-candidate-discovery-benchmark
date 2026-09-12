# 本轮复现入口

实际argv、PYTHONPATH、stdout/stderr在既有服务器按attempt保存，未公开绝对账号路径。
以下别名沿用原replication：PCDB_PYTHON为既有NumPy2.4.6/scikit-learn1.9.0/xgboost-cpu3.2.0
解释器；PCDB_C_CACHE为已批准良性C根，PCDB_E3_CACHE为原E3输入/前沿缓存根。
所需helper/runtime及输入SHA由执行器验证；PCDB_OUTPUT应为全新输出目录。

```sh
PYTHONPATH=src:scripts "$PCDB_PYTHON" scripts/run_m5_e3_state_precision.py --self-test
PYTHONPATH=src:scripts "$PCDB_PYTHON" scripts/run_m5_e3_state_precision.py \
  --study "$PCDB_C_CACHE" --previous "$PCDB_E3_CACHE" \
  --output "$PCDB_OUTPUT/precision-state-smoke-001" \
  --plan docs/experiments/M5_E3_STATE_PRECISION.md --mode smoke --wall-seconds 90
PYTHONPATH=src:scripts "$PCDB_PYTHON" scripts/run_m5_e3_state_precision.py \
  --study "$PCDB_C_CACHE" --previous "$PCDB_E3_CACHE" \
  --output "$PCDB_OUTPUT/precision-state-full-001" \
  --plan docs/experiments/M5_E3_STATE_PRECISION.md --mode full --wall-seconds 800
```

实际先核验smoke成功和旧点预测重建，再执行固定full；不按中间收益暂停/扩样或选择报告。
以上不是新的运行授权，禁止覆盖本次成功目录或为追求阳性重跑。

聚合只需现有Python/NumPy，实际本地NumPy2.4.2；使用NEW_STATS和NEW_VERIFY避免覆盖结果：

```sh
python3 -B scripts/analyze_m5_e3_state_precision.py --self-test
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B scripts/analyze_m5_e3_state_precision.py \
  --full docs/results/m5-e3-state-precision-20260912/full/result.json.gz \
  --full-sha 59a419b2486dbbe671fb42fcb0ad6f9945316f1c083cb9f7305988a347f4754a \
  --plan docs/experiments/M5_E3_STATE_PRECISION.md --output-dir "$NEW_STATS"
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B scripts/verify_m5_e3_state_precision.py \
  --directory docs/results/m5-e3-state-precision-20260912 --output "$NEW_VERIFY"
```

独立核验读取已发布statistics而非NEW_STATS；比较新聚合时需使用相同full/smoke布局的新副本。
本轮没有添加依赖，所有正式数据及候选级输出留在原服务器。
