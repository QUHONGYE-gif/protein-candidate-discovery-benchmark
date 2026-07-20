# Versioned manifests

本目录保存已通过 Schema 验证的小型、可审计 manifest 副本。大型 raw/processed
对象仍只保存在 COS。

Manifest 是不可变科学产物：发布后不原地改写。若来源、文件清单、checksum
或契约发生不兼容变化，必须创建新的 dataset version 和 manifest path。
