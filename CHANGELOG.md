# CHANGELOG

一行一条：日期 / 改了什么 / 为什么。原理、协议、数据细节写进 `docs/`，这里只记“什么时候动过什么”。

## 2026-11-11

- 4.6.54 真机 8 轮的经验落文档：`design/04 §5.1`（六条结论：客户端不会自己进下半、refresh 组刷新 NRE、scene 下半黑屏 0 轮结算、大厅传送卡在 `MapNPC:*-400001`、max_level 不解锁、`/debug/challenge` 无 UI 不能攻击）；`guide/03` 坑 34–38；`reference/05 §5.2` 给出 4.7.0 复测清单。
  依据：用户决定停用已废弃的 4.6.54 测试服客户端，换 4.7.0 前先把经验写下来。
- 混沌回忆 A 口径 M1（[design/04](docs/design/04-混沌回忆模拟器口径.md)）：`StartChallengeScRsp`/`GetCurChallenge` 回**两套阵容**（Danheng/LunarCore 同做法）；上半→下半改为**原地换组**（`scene.swap_challenge_group` 发 `SceneGroupRefreshScNotify`，不再重建 `SceneInfo`）；下半清空 `battle_ids` 防止重复上半结果提前结算；新增 `[endgame]` 配置（`moc_group_id=0` 按赛季时间自动选期、`unlock_all`、两个真机探针开关）。195 单测 + 全量回归绿，待真机。
- 新增 `docs/design/04-混沌回忆模拟器口径.md`：按 A 口径重做混沌回忆的计划（任意层可选、上下半双队原地换组、卡片三假设探针、M0–M4 里程碑）。
  依据：用户要求参考其他工程重做模拟器口径，先出计划；对照 DanhengServer/LunarCore 发现「两套阵容回包 + 下半原地换组」是缺失项。
- 新增 `scripts/import_srtools.py` + `data/vendor/srtools-20261111/`：从 srtools 公开数据 API 拉终局表（期名、`BeginTime/EndTime`、每半场全部 stage、target/分数）。结论：id 与旧表 843 条完全一致、无缺期；**当前赛季 = MOC 1036「物竞天择」(11-02~12-14) / PF 2027「取象为文」/ AS 3021「支配遗忘」**；130 条 MOC 上半其实有 2 个 stage（旧实现只取最后一个）。
  依据：用户指出关卡数据过期，要求先拉新数据再继续。

- 规则 v3：`AGENTS.md` 重写。只保留「不伪造验证」一条硬约束；默认直接做、做完汇报；仅删除不可重建数据前才问；完成由 Agent 凭证据自评；文档只写本文件一行；新增 A/B 玩法口径表与快车道。
  依据：v2 仍要求「按回滚成本分级 + 完成需人确认 + 契约文档」，实际开发中仍在等审批、等真机。用户决定：规则服务于开发，不束缚开发。
- 规则 v2：按回滚成本分级（R0/R1/R2）、验证分级 V0–V3、快车道、数据联网预授权；普通改动免同步 docs。
  原因：v1 的“改 3 个文件=大改”“客户端可见即需真机+人确认”把审批变成常态，人成为串行瓶颈。
- 新增 git 基线：`git init` 于 2026-11-11，基线提交 `86745c9`（157 文件，排除 `.venv/`、`logs/`、`saves/*.json`）。之后回滚 = `git revert`。
- 新增 `docs/notes/规则修订-2026-11-11.md`：v1 规则的证据化诊断、v1→v2→v3 改法映射、未决事项。
