# 可迁移执行入口

实际argv、环境路径、stdout/stderr保存在原研究服务器；下列别名不提供新执行授权。
BENIGN_STUDY为已批准的C缓存，E3_STUDY含原full-001、frontier-full-001和replication-full-001。
PCDB_PYTHON为既有Python3.11/NumPy2.4.6解释器；PCDB_OLD_RUNTIME/PCDB_OLD_HELPER分别为
原良性run-source-v3与原E3 source，脚本核对runtime/helper的精确SHA。

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PCDB_OLD_RUNTIME/src:$PCDB_OLD_RUNTIME/scripts:$PCDB_OLD_HELPER"
"$PCDB_PYTHON" scripts/analyze_m5_e3_selection_region.py --self-test
"$PCDB_PYTHON" scripts/analyze_m5_e3_selection_region.py \
  --study "$BENIGN_STUDY" --previous "$E3_STUDY" --output "$NEW_SMOKE" \
  --plan docs/experiments/M5_E3_SELECTION_REGION.md --mode smoke --wall-seconds 120
"$PCDB_PYTHON" scripts/analyze_m5_e3_selection_region.py \
  --study "$BENIGN_STUDY" --previous "$E3_STUDY" --output "$NEW_FULL" \
  --plan docs/experiments/M5_E3_SELECTION_REGION.md --mode full --wall-seconds 600
```

先检查smoke成功及旧端点精确重建再进入full；输出目录必须不存在。
独立聚合复核仅需Python/NumPy及本PR紧凑产物，本地实际NumPy2.4.2：

```sh
python3 scripts/summarize_m5_e3_selection_region.py \
  --directory docs/results/m5-e3-selection-region-20260911 --repository . \
  --output "$NEW_VERIFICATION_JSON"
```

全部区间与未定义支持数均保留，不覆盖已发布analysis.json；无新模型训练或标签揭示。
