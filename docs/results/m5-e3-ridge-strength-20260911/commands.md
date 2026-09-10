# 实际执行与复现入口

实际argv、环境和stdout/stderr留在既有服务器，下面使用符号路径。
既有Python3.11、NumPy2.4.6、scikit-learn1.9.0、xgboost-cpu3.2.0，无新依赖。
PYTHONPATH包含本次source、hxk4-followup-source-v1、generalization-source-v1、
原良性runtime的src/scripts以及原E3 source。所用helper逐文件SHA锁定于execution。
BENIGN_STUDY是原良性C缓存；E3_STUDY包含特征和hxk4-followup-full-001原模型/预测。
所有NEW目录须不存在；历史数据和成功产物只读。

```sh
python scripts/run_m5_e3_ridge_strength.py --self-test
python scripts/run_m5_e3_ridge_strength.py --study BENIGN_STUDY --previous E3_STUDY --output NEW_SMOKE --plan docs/experiments/M5_E3_RIDGE_STRENGTH.md --mode smoke --wall-seconds 90
# 接通的身份、原预测和选择精确重建通过，继续完整预列矩阵。
python scripts/run_m5_e3_ridge_strength.py --study BENIGN_STUDY --previous E3_STUDY --output NEW_FULL --plan docs/experiments/M5_E3_RIDGE_STRENGTH.md --mode full --wall-seconds 800
```

实际attempt为ridge-strength-smoke-001/full-001，各首次成功。smoke三拟合不算独立
科学重复；full120拟合复用40旧状态，未新增标签或状态。累计15分钟、1worker/线程、
1.5GiB内存、256MiB产物、5MiB回传限额；append-only attempts同步记录真实执行。
原始候选、模型和预测留服务器，不进入Git；compact只含汇总及身份/执行记录。

独立核验仅读取紧凑结果和源身份，使用现有NumPy，不加载候选/真值或实验runner：

```sh
OPENBLAS_NUM_THREADS=1 python3 scripts/verify_m5_e3_ridge_strength.py --directory docs/results/m5-e3-ridge-strength-20260911 --output NEW_VERIFICATION.json
```

使用bootstrap槽对应多项计数加权求均值，逐个删除状态计算影响范围。
全部端点表机械输出result的所有三池、三对比、manipulation、availability，无结果筛选。
