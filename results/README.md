# Results directory

M0–M4 正式 trajectory、metrics、logs、checkpoint、figure inputs 和 releases 保持其历史 COS
位置。ADR-0005 合入后新授权的 Governance v2 active run evidence 存放在 `/srv/pcdb/`；当前
#91/M5B 保持原路径。Gate B 后所有有效 accept/reject/inconclusive terminal 由独立 archiver
发布精简、不可覆盖的 COS archive；新流程普通 run 不写 COS。本目录仅保留这一政策说明。

可以提交体积较小、可审查且由正式 release manifest 引用的最终 summary table，但必须通过
独立 PR 明确说明来源和 scientific claim boundary。
