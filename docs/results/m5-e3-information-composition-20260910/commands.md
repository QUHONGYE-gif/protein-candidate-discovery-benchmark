# 执行命令与数据边界

源码、合同、原运行helper/输入/环境身份见full/execution.json。以下使用符号路径；实际argv、
PYTHONPATH、stdout/stderr和时间记录在批准服务器内保留，未将主机地址或用户路径公开。
不下载新数据，不安装新依赖；使用已有Python、NumPy2.4.6、scikit-learn1.9.0、xgboost-cpu3.2.0。

执行源码目录同时包含原版run_m5_e3_fixed_count_coverage.py；PYTHONPATH包含原benign C
runtime的src/scripts及原E3 source目录。BENIGN_STUDY和E3_STUDY分别指既有批准的数据缓存
与历史预测目录；两个NEW输出必须不存在。原数据、原执行源码和成功结果只读。

```sh
python scripts/run_m5_e3_information_composition.py --self-test
python scripts/run_m5_e3_information_composition.py --study BENIGN_STUDY --previous E3_STUDY --output NEW_SMOKE --plan docs/experiments/M5_E3_INFORMATION_COMPOSITION.md --mode smoke --wall-seconds 90
# 只在接通成功、原点预测精确重建且累计预算允许后执行。
python scripts/run_m5_e3_information_composition.py --study BENIGN_STUDY --previous E3_STUDY --output NEW_FULL --plan docs/experiments/M5_E3_INFORMATION_COMPOSITION.md --mode full --wall-seconds 800
```

过程append-only，不覆盖原attempt；单worker/线程，累计15分钟/1.5GiB/256MiB，紧凑返回上限5MiB。
所有标签选择限原64允许观测，预测和选点保存后评估。候选级文件和原始标签留服务器。
归档的smoke/full各有result.json.gz、execution.json、progress.jsonl、terminal.json，
另保留全部attempts.jsonl。smoke/full原服务器目录后缀均为001，没有失败或未报告的实验attempt。

本地独立算术复核只需已有NumPy，不加载原始数据或模型依赖。下列输出文件必须不存在：

```sh
OPENBLAS_NUM_THREADS=1 python3 scripts/verify_m5_e3_information_composition.py --directory docs/results/m5-e3-information-composition-20260910 --output NEW_VERIFICATION.json
```
