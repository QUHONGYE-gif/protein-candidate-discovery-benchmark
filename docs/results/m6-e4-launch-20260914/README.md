# M6 E4 四 assay 真实运行启动记录

用户于2026-09-14明确选择保留5 seeds和4/5一致性，允许wall预算改为76小时。
本记录对应Issue #107 / Draft PR #108，操作配置为 `e4-operation-v3.json`。

真实运行已于北京时间 **2026-09-14 00:27:06** 启动。指定服务器使用独立append-only
study目录和源码 `fd68935bf97cd7811c66f733066aa8e3677cb1dc`，不以报告提交替换执行源码。
[启动快照](launch.json)记录精确源码包、完整环境包清单的digest、operation、输入与探针hash。
不含序列、标签、内部路径或凭证。源环境完整清单及输入原始证据保留于服务器。

- 固定四个ProteinGym assay、1729–1733共5 seeds、4/5方向一致性。
- 320逻辑比较、260唯一轨迹、60个HF-only显式引用；每task256候选。
- member_jobs=2、RF内部及原生线程各1；RSS1.5 GiB、study产物2 GiB、自动重试0、COS写入0。
- 精确源码完整make check：994 passed / exit 0，Ruff/mypy/governance-check通过。
  [CI34768315562](https://github.com/QUHONGYE-gif/protein-candidate-discovery-benchmark/actions/runs/34768315562) SUCCESS。
- 新服务器无标签探针PASS；双线程三次5.4341/5.4919/5.5547秒，39360次调用上界外推
  **60.7318小时**。这是模型调用外推，不是已实测的全矩阵完成时间，仍需包括输入/IO/回读。
  硬截止为北京时间 **2026-09-17 04:27:06**，不自动续期或缩矩阵。
- 四assay真实输入已绑定，HF读取发生在预检通过、固定子集ID/特征之后。
  启动快照时2条轨迹已写完、第三条进行中，记录176个购买event，RSS约178.82 MiB。

上述“已写完”仅指轨迹文件与其checksum已产生；完整260轨迹及320比较尚未完成，新进程
独立回读也尚未完成，不能写成benchmark evidence complete。运行器完成矩阵后自动用保存的
score向量重算选择、HF/LF揭示、成本和评价，不重新训练全部RF；详见
[运行器及证据边界](../../M6_E4_RUNTIME.md)。全部通过后只写本地READBACK_VERIFIED记录，
不写Gate B/COS归档_SUCCESS，不自动运行E5/M7。

Gate A PENDING，依据既有用户指示执行例外；后续审批保留真实时间，不追溯伪造批准。
本次证据为development/directional，保持两项归因限制：净策略差不是LF纯因果效应，
高值区镜像不是等Pearson下的纯局部偏差。Docker及原三个容器仍停止，未删除数据卷。

本记录是启动时快照，后续状态必须从本次服务器study新鲜读取，不把静态RUNNING当实时状态。
