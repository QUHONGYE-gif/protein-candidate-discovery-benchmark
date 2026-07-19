# Data directory

本目录只记录数据存储政策。正式数据不进入 Git。

- 原始公开数据、processed data、features 和 splits 存放在 COS；
- `tests/fixtures/` 只允许保存小型、公开、具备来源说明的测试数据；
- 不允许保存内部序列、内部结果或秘密凭据；
- 每个正式数据版本必须具有 dataset manifest 和 SHA-256。
