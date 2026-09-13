# E4 ProteinGym 可观察输入核验

本记录对应 Issue #107、[四 assay 修订方案](../../M6_E4_PROTEINGYM_REVISION.md)。
状态 `OBSERVABLE_INPUTS_VERIFIED`；科学轨迹0，truth和zero-shot未读取，Gate A PENDING。

- [audit.py](audit.py)：服务器只读脚本，明确只打开清单、candidates与membership。
- [report.json](report.json)：四任务父清单和可观察文件size/hash、行数、子集hash。
- [readback.json](readback.json)：两个独立服务器进程重算的一致性记录与报告hash。
- [resource-options.json](resource-options.json)：绑定原无标签性能探针，重算四任务5/3 seeds。

脚本以 `--root` 指向已授权导入目录，以 `--output` 指向全新审计目录；输出目录独占创建。
两次审计只读取现有数据，不是科学轨迹重试。独立进程的时间字段不同，比较assays、
脚本与导入receipt hash完全一致。集合扫描包含父池全部可观察序列，但仅保存256子集的
有序ID列表hash，不持久化ID、特征或标签。输入为导入证据中的公开ProteinGym数据；
该证据身份的一致性不取代来源许可，也不改变后续再分发限制。

实际核验20份文件：四组dataset/processing/split/candidates/membership。
未核验truth文件hash，未生成feature matrix，未实现或启动完整真实runner；不写_SUCCESS。
原resource probe源码为 `e1fd7a8dbfd5b38f9430464bc2aba23670ecc2f5`，本次仅重算矩阵
资源需求，没有额外训练模型。5 seeds预计60.0830小时，超过54小时；3 seeds预计36.0498
小时，但仍需接受seed和统计规则变更，并完成运行前置工作。
