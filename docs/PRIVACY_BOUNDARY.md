# 公开数据与隐私边界

## 允许进入项目的内容

- 公开数据库和公开论文附属数据；
- 公开蛋白结构与公开模型分数；
- 基于公开标签生成的可控模拟数据；
- 本项目生成的公开 split、manifest、trajectory 和统计结果；
- 不包含私有信息的小型测试 fixture。

## 禁止进入项目的内容

- 团队内部序列、设计序列、受体、配体、binder 或靶点身份；
- 内部 ProteinMPNN、LigandMPNN、GNINA 或其他筛选结果；
- 内部结构预测、confidence、结构偏差、MD 或湿实验结果；
- 内部候选规模、筛选阈值、hit rate 或 score distribution；
- 使用内部数据选择后再在公开数据上汇报的参数；
- 任何永久云凭据、private key、access token 或 secret。

## 基础设施隔离

正式项目 Bucket 和 compute identity 只授予公开 Benchmark 所需权限。原则上不让同一运行
环境同时持有内部蛋白项目数据与凭据。COS 权限使用 least privilege 和 temporary
credentials；原始数据 read-only，运行结果写入独立 prefix。

## 提交前检查

提交者必须检查 diff、Git status 和新增文件大小。发现来源不明的数据、内部路径、credential
或无法确认许可证的文件时立即停止，不进行所谓“匿名化后上传”。
