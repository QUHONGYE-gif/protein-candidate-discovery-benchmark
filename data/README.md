# Data directory

本目录只记录数据存储政策。正式数据不进入 Git。

- M0–M4 原始公开数据、processed data、features 和 splits 保持历史 COS identity；
- ADR-0005 合入后新授权的 Governance v2 active data 存放在 `/srv/pcdb/data/`，以 SHA-256
  manifest 绑定；当前 #91/M5B 仍使用其既有授权数据面；COS 只接收新流程 Gate B 后的精简
  terminal archive；
- `tests/fixtures/` 只允许保存小型、公开、具备来源说明的测试数据；
- 不允许保存内部序列、内部结果或秘密凭据；
- 每个正式数据版本必须具有 dataset manifest 和 SHA-256。
