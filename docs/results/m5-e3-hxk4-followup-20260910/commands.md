# 实际执行与复现入口

完整实际argv、PYTHONPATH和stdout/stderr保留在原批准服务器研究目录；以下用符号路径。
服务器使用既有Python3.11、NumPy2.4.6、scikit-learn1.9.0、xgboost-cpu3.2.0，未安装依赖。
PYTHONPATH含本次source、上一轮generalization-source、原benign runtime的src/scripts和原E3 source。
BENIGN_STUDY为原良性C缓存；E3_STUDY含full-001特征及generalization-full-001原预测。
所有NEW目录必须尚不存在，原缓存和成功结果只读。

```sh
python scripts/run_m5_e3_hxk4_followup.py --self-test
python scripts/run_m5_e3_hxk4_followup.py --study BENIGN_STUDY --previous E3_STUDY --output NEW_SMOKE --plan docs/experiments/M5_E3_HXK4_FOLLOWUP.md --mode smoke --wall-seconds 90
# 接通及原两臂点预测精确重建通过，且仍在累计15分钟限额内。
python scripts/run_m5_e3_hxk4_followup.py --study BENIGN_STUDY --previous E3_STUDY --output NEW_FULL --plan docs/experiments/M5_E3_HXK4_FOLLOWUP.md --mode full --wall-seconds 800
```

实际smoke/full后缀均001、首次成功，详见[attempts](attempts.jsonl)。本次80新拟合与接通2拟合
分开；旧20只读诊断不算新fit。主结果固定新40状态，不因接通或半组结果改变后续执行。
单worker/线程、1.5GiB内存、256MiB产物、5MiB紧凑回传、新费用0。
源码/便笺/输入/版本/状态身份见[execution](full/execution.json)，实际完成见[terminal](full/terminal.json)。

独立核验仅需现有NumPy，不加载模型、候选或真值：

```sh
OPENBLAS_NUM_THREADS=1 python3 scripts/verify_m5_e3_hxk4_followup.py --directory docs/results/m5-e3-hxk4-followup-20260910 --output NEW_VERIFICATION.json
```

核验用bootstrap槽对应的多项计数加权均值，不调用原runner；逐个删除状态重新算影响，
并对照上一轮原始紧凑端点。全部端点表直接由本次result生成，未挑选结果替换主端点。
