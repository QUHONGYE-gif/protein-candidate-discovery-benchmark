## Tracking

- GitHub Issue（达到独立 Issue 门槛时；否则写父 Issue 或 `N/A`）：
- Multica issue(s)：
- Close intent（仅在验收完成且合并应关闭 Issue 时写 `Closes #...`；否则写 `Related #...` 或
  `N/A`）：

## PR classification

只能选择一个：

- [ ] Major / semantic PR
- [ ] Maintenance batch PR

Major / semantic PR：

- [ ] 已说明 contract/ADR、兼容/迁移与 public-interface 影响
- [ ] 已形成可审阅的最小语义单元，且未与 maintenance 混批

Maintenance batch PR：

- [ ] 每项都是 atomic commit，且不改变科学语义、数据身份、执行边界、存储责任或公开接口
- [ ] 父 Issue comment/checklist 已记录每项变更与测试
- 最早触发点（选一个）：
  - [ ] 累计 10 个逻辑小变化
  - [ ] 当前 Issue/milestone 结束
  - [ ] 下一段实验需要其中信息

## Scope

说明本 PR 完成的单一目标。

## Out of scope

明确本 PR 不处理的内容。

## Verification

- [ ] targeted tests
- [ ] `make check`
- [ ] diff scope reviewed

## Data and privacy boundary

- [ ] 未加入私有序列、内部结果、秘密凭据或大型 artifact
- [ ] 新增公开数据具有来源、license 和 manifest
- [ ] hidden-label boundary 未被绕过

## Protocol and storage impact

说明是否改变 scientific semantics、data identity、dataset/split/feature/run schema、执行边界、
存储责任、public interface、COS object layout 或 held-out authorization。Maintenance 必须写
“无”。

## Evidence boundary

分别说明：

- Engineering evidence：
- Server/runtime evidence：
- Scientific evidence：

CI green 不等于 scientific gate passed。

## STATUS impact

- [ ] 本 PR 不因普通 merge、CI、run、retry、日志、plot 或 maintenance batch 更新 STATUS
- [ ] 若更新 `docs/STATUS.md`，本 PR 已绑定 Gate A scientific transition、Gate B terminal 或
  milestone closeout 的可验证记录
