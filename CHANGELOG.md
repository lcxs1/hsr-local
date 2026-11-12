# CHANGELOG

一行一条：日期 / 改了什么 / 为什么。原理、协议、数据细节写进 `docs/`，这里只记“什么时候动过什么”。

## 2026-11-12

- **代码审查后按优先级修复（P0/P1/P2）**：按 [docs/notes/2026-11-12-代码审查.md](docs/notes/2026-11-12-代码审查.md) 修掉 1 高危 + 6 中危 + 一批低危。P0：`LineupState.set_slot` 钳制 `0<=slot<team_size`（原来对客户端可控 slot 无上界，`slot=2^31-1` 会 append 数十亿个 0 → OOM；负索引静默覆盖末位也一并修），`on_replace/on_join/on_change_leader` 对越界 slot/leader 记日志并忽略。P1：`router.dispatch` 把回包 `session.send(rsp)` 纳入 try（原来编码失败会永久杀死 session worker）+ `session._run` 单包异常后 continue 而非退出循环；`on_scene_map_info` 数量上限 64（防响应放大）；`challenge.battle_info` 轮次为 0 不再重置满轮；`save.flush` 时间戳移到写成功后 + `os.fsync`。P2：阵亡数 `phase_start_dead_avatars` 重打回退；`next_battle_id` 归一化 >=1；删 `on_enter_scene` 死代码；`codec.read_varint` 边界检查；`check_docs` 盘符 `C:` 误判 scheme 修复；kcp 裸 `except` 改 `except Exception`；清 AGENTS 残留 docstring；非回环监听启动警告；回放队伍断言改全量比较 + `replay` cfg 深拷贝与 `names.index` 防御；`freesr_sync._write` OSError 兜底；`decode` 复用 `frame.parse` 校验帧长。新增 `tests/test_hardening.py`（7 例）。**238 单测 + `check.ps1` 全绿（ruff 过）**。
  依据：2026-11-12 代码审查报告，用户要求按优先级修复。

- **去除 AGENTS.md 协作约束（彻底放开）**：删除 `AGENTS.md`，清理 README/docs 里对它全部 16 处引用——协作规范行、人机协作规范 v3 行、A/B 口径、数据快车道、「删存档先问」等一律改成直接陈述事实，不再引规则。同时把过期数字统一改成实际值（单测 227→231、`guide/03` 规模 193/74→231/78、`guide/02` unittest 221→231）、README 的 freesr 数值标签顺序纠正（光锥 87 / 遗器 418）、混沌回忆「待真机/未真机」改「已真机」。`check_docs.py` 0 问题、231 单测绿。
  依据：用户决定去除 AGENTS.md 限制、彻底放开；先跑 `check_docs.py` 并核对实际单测/数据条数。

## 2026-11-11

- **收工：交接快照重写**（[notes/2026-11-11-混沌回忆状态快照](docs/notes/2026-11-11-混沌回忆状态快照.md)）：改成一份完整交接文档 —— 现状一句话、代码在做什么（`advance` 各模式已知结论）、今晚修掉的 6 条（症状→根因→修法→证据）、**唯一未解决项**（下半战斗卡进场动画：两个实验的失败数据 + 已排除项 + `/debug/challenge?second=1` 探针 + 3 个备选改法）、复测命令与判据、日志/截图判据、关键 ID、其它搁置项、9 条教训。`docs/README`、`guide/03 §5`、`design/04` 顶部同步指向。
  依据：用户收工，要求把关键信息落文档方便交接。
- **下半战斗问题的两轮实验结论 + 新探针 `/debug/challenge?second=1`**：① `sync` 下下半**等 45 秒**再打仍卡（`battle stats turns=0 cost=9s`，同轮上半 `turns=1 cost=15s`）→ 不是加载竞态；② `advance=declare` 不重载 stage，但客户端**不发 `SceneCastSkill`** → `group_info` 声明出来的组里没有可攻击的怪。于是把变量切到战斗本身：`start_direct(..., second=True)` + `GET /debug/challenge?id=5501&second=1` 直接把下半当第一场战斗打，用来分辨“下半战斗包不对”还是“换半场把客户端状态弄坏了”。231 单测 + 回放 + ruff + 结构检查绿。
  依据：`logs/test/test-20261111-234217.*`（等 45 秒）、`test-20261111-234635.*`（declare 无 cast）；客户端 Player.log 对战斗场景失败没有任何日志。
- **新增 `advance = "declare"`（下半卡进场动画的对照实验）**：不推场景、不触发客户端重载 stage，改用 `GroupRefreshInfo.group_info`（与 `SceneInfo.entity_group_list` 同一个 `SceneEntityGroupInfo` message）原地**声明**下半的迷宫组，走和上半进场景完全一样的路径；`services/scene.py` 新增 `declared_group_notify`。`[endgame] advance` 支持 `declare|sync|inplace|scene|client`（默认仍是 `sync`），取证脚本 event 规则认 `challenge advance (declare)`；230 单测 + 回放 + ruff 绿。
  依据：用户 23:39 描述「下半进场景、看到 NPC、攻击了，正常会近战，但卡在进战斗的动画（类似『弱点』），几秒后直接结算」——`sync` 路线让客户端 `AdventurePhase._EnterStep2` 重载整个 stage，玩家 5–6 秒后开打，战斗进场卡死；`declare` 用来隔离“重载 stage”这个变量。
- **`sync` 路线真机通过；新增上下半战斗自证日志**：23:25 / 23:27 两轮 4.7.0 真机上下半都能进、能按 F、能结算（报告 A 段全绿）。剩下的新症状是**下半战斗黑屏 ~15 s 后自己 WIN → 直接结算页**，服务端侧看不出问题，所以补两行日志：`battle stats: battle=… rounds=… turns=… auto_turns=… cost=…s monsters=… damage=… avatars=…`（`handlers/battle.py`，从 `PVEBattleResultCsReq.stt` 取）和 `challenge battle waves: stage=… waves=[[…]]`（`services/challenge.py`），取证脚本加 `battle-stats` / `waves` 两条 event 规则。229 单测 + 回放 + ruff 绿。
  依据：`logs/test/test-20261111-232502.*` 全黑截图 `-48/-50/-51/-52`；`data/endgame.json` 的 `stages[30126012]` 与 pearl-sr `StageConfig.json` 的 `MonsterList` 完全一致，排除“本地没有怪物数据”。
- **阶段切换改成参考实现的 `sync` 路线（下半卡近战第二轮修）**：`[endgame] advance` 默认从 `inplace` 改成 `sync` —— `ChallengeLineupNotify` + `SyncLineupNotify` → `PVEBattleResultScRsp` → `SyncServerSceneChangeNotify` + `EnterSceneByServerScNotify`（整份新 `SceneInfo`，`entity_group_list` 里声明 node2 的迷宫组），**不再发任何 `SceneGroupRefreshScNotify`**；`inplace`/`scene`/`client` 保留为 A/B。测试脚本 event 规则认 `challenge advance (sync)`；回放链改跑 `sync` 并断言“sync 段不许出现组刷新 + 包序”；新增单测 `test_advance_sync_uses_reference_packets`（228 单测绿、`check.ps1` 除 `AGENTS.md` 死链外全绿）。
  依据：`logs/test/test-20261111-230753.*` 三轮真机 —— 去掉卸载后 `Already Exists` 消失，但每次 advance 仍有一条 `_OnSceneGroupRefreshScNotify` → `MapDef._ActiveServerHoyoGroup` NRE（4.7.0 不认没在 `SceneInfo` 里声明过的 server group），下半 NPC 看得见、按 F 只有近战动作、advance 后零 `SceneCastSkill`；同一时刻客户端自己重载 stage（`AdventurePhase._EnterStep2`），说明它在等“服务器场景变了”的信号。参考 LunarCore `ChallengeInstance.advanceStage`（`SyncServerSceneChangeNotify` + `EnterSceneByServerScNotify`）。
- **新增接手快照** [`docs/notes/2026-11-11-混沌回忆状态快照.md`](docs/notes/2026-11-11-混沌回忆状态快照.md)：把这一晚的全部结论（6 条已确认根因 + 证据路径）、当前代码行为、下一轮真机判据（报告 A 段三行）与 4 个候选改法、日志分析命令、关键数据 id 写进一篇；`docs/README §2.0`、`guide/03 §5`、`design/04` 顶部都指向它。用于上下文压缩 / 换人接手。
  依据：用户要求先落文档再汇报测试结果。
- **下半卡近战/黑屏的根因修掉**：换半场默认**不再卸载旧迷宫组**（4.7.0 把 `refresh_type=LOADED` 的“卸载包”当成「激活组」→ `Reload ServerGroup Error, Already Exists!!! ServerGroupID: 9` → `MapDef._ActiveServerHoyoGroup` NRE → 客户端黑屏「弱点」/卡近战、advance 之后一个 `SceneCastSkill` 都不发）。新增 `[endgame] unload_old_group`（默认 false；true = 用 `refresh_type=UNLOAD` 整组卸载，未真机）。取证脚本 A 段新增三行判定（客户端组刷新报错 / 下半是否发出 cast / 大厅传送拦截），并修掉多轮尝试时 “cast 统计串轮”“误报上半重打”。227 单测绿。
  依据：`logs/test/test-20261111-224811.*` 三轮真机：上半都能打，advance 后客户端必报 `Already Exists ServerGroupID: 9`、`+6s/+14s` 截图黑屏带「弱点」，服务端零 cast。
- **大厅传送：默认恢复切场景 + 探针开关**：`[endgame] lobby_teleport` 默认改回 `scene`（照常切大厅），只保留 `ignore` 作为“宁可不进大厅也别卡”的开关。原因：22:38 真机证明忽略传送后大厅 UI 与挑战水晶一起进不去（“不卡了但也进不去”），而 21:02/21:05 说明大厅**不是每次必挂**。配套：`scripts/game_test.ps1` 新增 `-LobbyEntry <id>` / `-LobbyTeleport <mode>`（临时改 `config.toml`，跑完自动还原）+ `-IntervalShotSec` 定时截图 + 按包截图（`EnterScene`/`InteractProp`/`GetChallenge*`/`StartChallenge*`/`SceneCastSkill`… 抓点击瞬间的 UI）；大厅入口没有水晶道具时在出生点合成三块水晶（`scene_service.spawn_crystals`）。
  依据：22:31 传送卡死（客户端 `MapNPC:227-400001` NRE → SDK 弹框关闭会话）与 22:38 三轮“传送被忽略、无法进入”两轮日志。
- **大厅传送兜底（同日早些时候，已被上面一条修正）**：曾把 4.7.0 的「传送」默认忽略以避免 `MapNPC:227-400001` 卡死；实测会连带堵死入口，已恢复默认切场景。
- **测试脚本改写**（[guide/02 §3-§5](docs/guide/02-测试与回归.md)）：`scripts/game_test.ps1` 改成 `-Scenario challenge|core`，默认 `challenge`（4.7.0）——每 0.5 s 扫服务端日志/`packets.log` 打事件时间线，事件驱动截图（开始挑战、每次战斗包、**换组后 +0/+6/+14/+25 s**、结算、退出），全程镜像客户端 `Player.log`（原文件每次启动会被覆盖），结束后写 `<run>.report.md`（A 结论摘要：上半/阶段切换/下半/cast & QuitBattle 次数/大厅传送拦截/MapNPC 加载失败/异常；B 时间线；C 请求序列；D 下半窗口客户端日志；E 异常去重；G 服务端尾部）+ `<run>.client.log` + `<run>.packets.log`；新增 `-AnalyseOnly <run>.server.log` 重分析已落盘日志、`-MaxMinutes`/`-StopAfterSettle` 自动收工。`core` 保留旧流程。`services/challenge.py` 补 `challenge settle:` 一行日志、`handlers/scene.py` 补 `lobby teleport ignored` 一行日志供报告判定。
  依据：下半黑屏需要「换组后那十几秒客户端在干什么」的证据；旧脚本只有定时截图 + 末 500 行日志，拿不到包序与下半窗口。
- **混沌回忆逻辑重写**（[design/04 §5.4](docs/design/04-混沌回忆模拟器口径.md)）：`services/challenge.py` 改成阶段状态机（`phases: list[Phase]`，`enter/enter_tierce/advance/enter_second_half`），阶段切换包序对齐参考实现（`ChallengeLineupNotify` → 角色组刷新 → 卸旧组 → 装新组，入口不变不重建 `SceneInfo`）；`[endgame] advance` 改 `inplace|scene|client`（旧 `refresh/none` 仍可读）。同时修：额外阵容 proto `index` 改 0、退出挑战发 `ChallengeLineupNotify(LineupNone)`+`SyncLineupNotify`、`CurChallenge.round_count/dead_avatar_num` 按战斗结果累计、`GetCurBattleInfoScRsp` 回空但存在的 `battle_info`（4.7.0 `_OnGetCurBattleInfoScRsp` NRE）、新增 `EnterChallengeNextPhase`/`RestartChallengePhase` handler、`GetCurSceneInfo`/场景装配改用场上这一队。221 单测 + `check.ps1` 全绿、回放新增上下半链；**未真机**。
  依据：4.6.54 8 轮 + 4.7.0 5 轮真机反复出现「下半黑屏/节点二战斗被拒」，旧实现每次只打补丁；对照 HyacineCore 4.0（`NextPhase`/`PacketSceneGroupRefreshScNotify`/`LineupInfo.ToProto`）与 pearl-sr 逐条对齐。
- 星启模式节点切换加 `advance = "refresh"` 探针：新增 `challenge_service.advance_tierce_refresh`（原地换组，`SceneGroupRefreshScNotify`），`_enter_second_half` 对 tierce 也走同一个 `[endgame] advance` 开关，默认改 `refresh`。依据：4.7.0 真机 21:35 节点一打完、重建 `SceneInfo` 进节点二时客户端「场景不对 + 战斗黑屏」，与 4.6.54 下半同症状，唯一没试过的路是原地换组。211 单测绿。
- 传送卡死的时间假设**证伪**：7 s 成功 / 14 s 卡 / 14 s+压到 25 s 仍卡，5 轮数据表进 `design/04 §5.2`；`lobby_grace_seconds` 回退默认 0（代码留着当探针）。同会话内卡过后连点 5 次传送都救不回，只能重启客户端。

- 4.7.0「星启模式」协议落地（[design/04 §5.2](docs/design/04-混沌回忆模拟器口径.md)）：实现 `StartChallengeTierceCsReq`（cmd 8986）/`LeaveChallengeTierceCsReq`（cmd 8987）。一层 = 3 个节点、客户端一次下发 3 套 `lineup_tierce_list`；`ChallengeState` 加 `tierce/stage_index/tierce_teams`，新增 `challenge_service.enter_tierce`/`advance_tierce`（节点间重建 `SceneInfo`），第 3 个节点打完才 `ChallengeSettleNotify`。修「混沌回忆第 12 层编队点『混沌回忆』按钮没反应」。76 handlers、全量 `check.ps1` 绿，待真机。
  依据：真机 `packets.log` 显示客户端连发 5 次 `StartChallengeTierceCsReq (cmd 8986) 93 bytes`、服务端记 unhandled，按钮才没反应。
- 4.7.0 真机第 1 轮：三张终局卡片（混沌回忆/虚构叙事/末日幻影）在 4.7.0 **自动解锁**；大厅传送仍卡在与 4.6.54 **同一个** `MapNPC:227-400001`（`MapNpcDef.get_ConfigEntityPath` NRE），确定是客户端资源问题、服务端补不了。下一轮绕开大厅走「星际和平指南 → 混沌回忆」面板，`lobby_props` 改 `crystals` 做对照。写入 `design/04 §5.2`、`reference/05 §5.2`。
  依据：用户 4.7.0 真机反馈 + `Player.log` 栈；解锁门槛被证伪后不再在服务端猜解锁条件。

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

