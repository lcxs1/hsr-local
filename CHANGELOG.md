# CHANGELOG

一行一条：日期 / 改了什么 / 为什么。原理、协议、数据细节写进 `docs/`，这里只记“什么时候动过什么”。

## 2026-11-13

- **收工：P0 命令号补齐做到一半 + 交接清单**：探测脚本改成自适应轮询（≈0.2 s/id）并新增
  `--skip-known` / `--skip`；扫完 `0 300 800 1000 1500 1600 1800 1900 2200 2500 2900 3100 3300`
  等块，`logs/probe_ids.json` 142 → 194 条；对齐出的 137 个请求名里有 **111 个还缺响应号**，
  已算好落在 64 个 100 块并写进交接文档。中途因为没加 `--skip-known`，在 `0-99` 块打中
  心跳回包 id（18）：客户端 `NetworkManager` NRE → 停止心跳 → 15 s 会话超时 → 之后探测全 404；
  且客户端是提权进程，`Stop-Process`/`taskkill`/`CloseMainWindow` 都无法关闭，只能人工关。
  剩余工作、续跑命令、坑与 P1–P4 计划全部写进
  [notes/2026-11-13-4.8协议攻坚与验收 §5](docs/notes/2026-11-13-4.8协议攻坚与验收.md)。
  依据：`logs/probe_ids.json`、`logs/packets.log` 19:23–19:25 段、`check_proto` 140 条/31 handler/48 skipped。

- **4.7.51 真机验收：换队伍 / 换皮肤 / 进战斗 / 战斗结算全部通过**：18:53 真机会话里两次 `ReplaceLineupCsReq`（换队）、`DressAvatarSkin`/`TakeOffAvatarSkin`（换皮肤）、两场 `StartCocoonStage`+`PVEBattleResultCsReq`（`BATTLE_END_WIN`）全部无异常，整段 `handler .* failed` = 0。根因都是 4.8 字段改名：`LineupSlotData.slot→slot_id`(9→13)、`JoinLineupCsReq.slot→slot_id`(1→15)、`PVEBattleResultScRsp.check_identical→字段11 DLMDAIOKKEO`、`Challenge.star/score_two→stars/score_second`、`ChallengeGroup.taken_stars_count_reward→taken_challenge_reward`、`GetPlayerBoardDataScRsp.current_head_icon_id→head_icon_id`、`GetLineupAvatarDataScRsp.avatar_data_list→lineup_data_list`；全部写进 `proto/4.7.51/field_map.json`（只改名不动号），handler 侧不改。**教训**：空请求演练跑不出这类 bug，改成"填满字段"后 31/31 通过（`scripts/exercise_handlers.py`）。
  依据：`logs/packets.log` 18:53:29–18:57:02 段；[notes/2026-11-13-4.8协议攻坚与验收](docs/notes/2026-11-13-4.8协议攻坚与验收.md) §2。
- **4.8 命令号反查：79 → 140 条，handler 23 → 31**：新增四个可复用脚本 —— `scripts/probe_cmd_ids.py`（给客户端发非法包体，用 `Deserialize <msg> meet exception` + `_OnXxxScRsp` 栈名反查响应号，≈0.68 s/id）、`scripts/align_login_seq.py`（4.6 登录序列【名字+包长】对齐 4.8 序列【id+包长】，已知 id 当锚点，137 条里 119 条为段长完全相等的高置信配对）、`scripts/merge_cmd_ids.py`（要求 req/rsp 同 100 块双端齐全才入表，已有条目不覆盖）、`scripts/probe_to_cmdid.py`（单次探测结果转表项）。新增条目含 `GetPlayerBoardDataCsReq/ScRsp 2883/2854`、`GetMissionDataCsReq/ScRsp 1283/1254`、`GetMarkItemListCsReq/ScRsp 559/582`、`GetQuestDataCsReq/ScRsp 983/954`、`GetLineupAvatarDataScRsp 743`、`SwitchLineupIndexScRsp 788`、`GetCurBattleInfoCsReq/ScRsp 173/133`、`InteractPropScRsp 1442`、`GetUnlockTeleportScRsp 1466` 等。**教训**：4.8 的 id 与 4.6 无任何算术关系，社区表（HyacineDH / FreeSR）全不对，只能拿客户端当 oracle。
  依据：`logs/probe_ids.json`、`logs/cs_ids.json`、[notes/2026-11-13-4.8协议攻坚与验收 §3](docs/notes/2026-11-13-4.8协议攻坚与验收.md)。
- **进游戏卡加载 13–14 s → 4.7 s**：两个叠加原因。(1) hotfix 的 `ifix_version=17102685` 在本地 CDN 无包，客户端 `M_IFixV.bytes` 连续 404 并按 3 s/9 s 退避重试——`data/hotfix.json` 的 4.7.51 条目改为 `ifix_version="0"` + `output_0_…` URL，不再请求 ifix；(2) 4.8 改了 `PlayerSettingInfo` 的子消息结构，空/缺字段让 `RPG.Client.PlayerModule._CmdGetBasicInfoScRsp` NRE——`handlers/login.py` 的 nested 模式改成按 schema 遍历、给所有 message 类型子字段填空实例（`is_repeated` 跳过），新版本加字段无需改代码。
  依据：`logs/packets.log` 18:48/19:03 两次登录（连接→可移动 4.7 s / 5.8 s）。
- **回归**：`unittest` 250 OK（更新 `test_partial_command_table` 里"GetMissionDataCsReq 不在表里"的过期断言）、`check_proto` 31 handlers / 48 skipped / OK、`exercise_handlers` 31/31、五链 replay PASS、`check_docs` ok。
  依据：本轮收工前统一跑。

- **4.7.51 真机首登通过：4.8 描述符快照 + 字段改名 + partial cmd 表**：新增 `hsr/protocol/dynpb.py`（私有 `DescriptorPool` 从 `proto/4.7.51/classes.desc` 运行时建类）和 `hsr/protocol/pb.py`（handler 统一取类，`Session._run` 用 contextvar 绑定当前 profile 的快照）；4.8 改名的字段用 `proto/4.7.51/field_map.json` 在加载时改回 handler 名字（**字段号不动**），枚举按值编号兼容 4.6 名字，`MazeGroup→MapInfoGroup` 走消息别名；`versions/4.7.51.toml` 加 `cmd_table = "partial"`，`Router.add` 对 79 条表外的 56 个 handler 只跳过不报错，`check_proto.py` 打印 skipped；新增 `tests/test_dynpb.py`（6 条）。真机 18:04 登录链 cmd 73→83→583→383→753→1407，角色进入 20313 可跑图；250 单测 + check_proto + replay + check_docs 绿。
  依据：`logs/test/debug-screen.png`、`logs/packets.log` 18:04:11 段、[reference/06 §9](docs/reference/06-4.7.51接入.md)。
- **接入 4.7.51 客户端（`OSBETAWin4.7.51` / build `17100438`）**：新增 `versions/4.7.51.toml`、`data/hotfix.json` 的真实 CDN 条目；`config.toml` 默认 profile、`game_test.ps1 -Client 4.7.51`、`hsr/config.py`/`version.py` 默认值同步；新增 [reference/06](docs/reference/06-4.7.51接入.md)。离线：244 单测 + `--strict` + `query_gateway`（lua 17099231）通过。真机排查（reference/06 §8）：4.7.51 实为 **4.8 beta**，cmd id 与字段号整体重排；已修 `GateServer` 线格式（客户端能过资源校验、UDP 握手成功，首包 cmd 73），新 schema/cmd 表从 ppSR 取出到 `proto/4.7.51/`（同日的首登成功记录见上一条）。
  依据：`E:\SiFu\HSRSiFu\StarRail_4.7.51` 的 `GameAssembly.dll` / `InstallVersion.bin` / `M_ArchiveV.bytes` + ppSR 4.7.5X 的 `misc/hotfix.json`。

## 2026-11-12

- **GitHub 发布前整理（仓库卫生 + 去机器化）**：新增 GPL-3.0 `LICENSE`、`.gitattributes`、`requirements.txt` / `requirements-dev.txt`、`saves/.gitkeep`；`.gitignore` 补齐 `.tmp*/`、`*.pre-lobby`、编辑器/系统文件；从索引移除 `data/endgame.json.pre-lobby`（1.1 MB 备份）与第三方 goproxy CA `firefly-go-proxy-ca.crt`；`config.toml` 的 `client_dir` 清空（4.7.0 本地 CDN 改为显式开启）、清掉 `AGENTS.md` 残留引用；`scripts/check.ps1` 的 ruff 查找不再写死别项目路径；README 重写为 GitHub 入口（环境要求、安装、目录结构、免责声明、许可），docs/README §3 数字更新为 242 单测 / 79 handler；`PROVENANCE.json` 去掉机器本地路径。
  依据：上传 GitHub 前的最终整理；242 单测 + `check.ps1` + ruff + `check_docs.py` 通过。

- **发布前隐私清理**：审计发现 4.6.54 基线样例里有真实玩家昵称/签名/UID，且 Git 历史作者邮箱为真实邮箱。已把样例中的 `nickname` / `signature` / `uid` 脱敏为示例值；用 `git filter-repo` 重写全部 49 个提交，作者/提交邮箱统一为 `lcxs@users.noreply.github.com`，并把 `data/endgame.json.pre-lobby` 与第三方 goproxy CA 从历史移除。当前树与全部历史均检索不到旧邮箱、昵称、签名或 UID。
  依据：上传 GitHub 前审计；`git fsck`、`check_docs` 通过。

- **收工：下半黑屏交接快照 + 全天整理**：新增 [notes/2026-11-12-下半黑屏收工快照](docs/notes/2026-11-12-下半黑屏收工快照.md)——今天做完的 6 件事、唯一未解决项（下半战斗黑屏）、**9 类已排除原因（每条附验证方法）**、当前代码状态与探针开关、复现/取证命令、关键 ID、下一步（Procmon 步骤）与 8 条教训；同时更新 `docs/README` 文档地图（正式服抓包条目改成「已抓到真机数据」+ 新增快照条目）、`guide/03 §5` 交接指向。242 单测 + `check.ps1` 绿。
  依据：2026-11-12 全天（抓包 + 7 轮真机 18:57/19:17/19:26/19:32/19:41/19:46/19:57），用户决定收工。

- **下半黑屏收敛：连做 4 轮 A/B，把「换半场 / 战斗包 / 结果回包 / 第二场状态 / 下半 stage 数据」全部排除**：19:26（结果回包补 `battle_avatar_list`）、19:32、19:41（`avatar_swap=skip` → 客户端 0 次 cast 卡死）、19:46（探针 `second=1` 把下半当第一场 → 同样黑屏）、19:57（`battle_stage_override=30126011` 让下半用上半 stage 数据 → 仍然黑屏、turns=0）。**下半这场战斗在正常流程、探针流程、换 stage 数据三种条件下都失败**，而客户端在 9 秒里只发心跳、不发请求——它在等一次本地加载完成。已排除项与证据整理进 [抓包方案 §11](docs/notes/2026-11-12-正式服抓包方案.md)；探针（`avatar_swap` / `battle_stage_override`）保留在 config.py，config.toml 均已归零/回到 renumber。下一步要么用进程监视器看那 9 秒的客户端行为，要么拿一个已知能跑下半的实现做对照。
  依据：logs/test/test-20261112-192639 / 193244 / 194125 / 194611 / logs/packets.log（19:56 探针）+ 官方抓包 #519/#604/#610/#611/#658。

- **下半黑屏第五轮：把「换角色实体 id」做成 A/B 开关，先按 skip 跑**：19:32 真机（结果回包已按官方补齐）仍是两层同症；同时拿到客户端施放包的字段（`cast=21 hit=[] assist=[1010] attacked_by=5`，上半是 `cast=15 assist=[1000] attacked_by=1`）——**客户端确实把下半的怪当成可打目标**，施放包没问题。剩下的可疑点收敛到换半场里唯一还会动**客户端状态**的一步：删掉角色实体 1-4 再加 5-8。新增 `[endgame] avatar_swap = renumber|reuse|skip`（默认 renumber = 官方做法），`config.toml` 先设 skip 做 A/B；单测/回放固定 renumber，不受探针影响。242 单测 + check.ps1 绿，真机待复验。
  依据：logs/test/test-20261112-193244.packets.log 的 `cast skill req` 行 + 官方抓包 #604。

- **下半战斗黑屏第四轮：结果回包按官方补齐**：19:26 真机（资源版本回显已生效）仍是两层同症（上半 turns=1、下半 turns=0 + 9 s）。复盘发现一个结构性不对称：**官方 `PVEBattleResultScRsp` 是 1827 字节并带整份 `battle_avatar_list` + `check_identical`，我们只回 4 个字段**。挑战战斗用的是额外阵容（LineupChallenge/2），客户端要靠这份数据给该阵容收尾——第一场战斗本身能打（不依赖它），第二场进不去（依赖第一场已正确收尾），正好解释「换半场怎么改都没用、只有第二场失败」。`handlers/battle.on_pve_result` 现在把该场 `battle_info.battle_avatar_list` 回填进结果包并置 `check_identical=true`。另在 `on_cast_skill` 加一条 debug 日志（cast/hit/assist/attacked_by），下一轮 packets.log 就能看到客户端施放包到底带了什么。242 单测 + check.ps1 绿，真机待复验。
  依据：官方抓包 #604 与现实现逐字段对比；logs/test/test-20261112-192639.*（两层 FAIL）。

- **下半战斗黑屏第三轮：两层都复现 → 补上资源版本回显**：19:17 真机按新脚本打了两层（5501/5502），**两层症状完全一样**（上半 turns=1 正常、下半 turns=0 + 固定 9 s），所以不是 stage 数据问题，而是「同一局挑战的第二场战斗」通用失败。逐帧比对官方 #604-#658 后确认换半场 10 个包（含 group 9 删除、group 10 声明、角色实体换 id、四条阵容包）与官方逐字段一致，排除了换半场。随后发现一个真实的协议缺口：客户端在每次开战前发 SyncClientResVersionCsReq（LJKMPIMKOMO=资源版本），官方 #651→#655 **原样回显**，而私服走通用空回包——等于告诉客户端「服务端资源版本 = 0」。新增 handlers/player.on_sync_res_version 回显版本号并注册；新增 tests/test_res_version.py（2 例）。244 单测 + check.ps1 绿，真机待复验。
  依据：logs/test/test-20261112-191725.*（两层判定）+ 官方抓包 #651/#655；同一轮里 battle_stage_id/每怪空 ItemList/FGOAKDCBIOE 已先补（见上一条），本次不重复改。

- **新增一键复测脚本 + 运行判定器**：`scripts/test_lower_half.ps1`（检查 `advance = official` → 复用 `game_test.ps1` 起服务端/代理/游戏收证据 → 结束后自动判定下半有没有真打，`-AnalyseOnly` 可只分析旧日志）与 `scripts/analyze_run.py`（按 challenge id 汇总每半场 `turns`/`cost`、换半场模式与包数、advance 之后客户端 cast 次数与延迟，PASS/FAIL 走退出码）。判据固化：`turns>0` = 真打，`turns=0` + ≤15 s = 战斗场景加载超时判胜。用 2026-11-12 18:57 那轮验证过输出（正确判 FAIL 并指出 +2923 ms 的 cast）。
  依据：两轮真机都要人工翻日志，把判定固化成脚本；跑 `check.ps1` 全绿。

- **下半黑屏第二轮：换半场真机确认通过，战斗包按抓包补齐 wave 字段**：真机（`test-20261112-185701`）证明 `advance=official` 生效——`challenge advance (official) … packets=10` 之后 2.9 s 客户端就对下半的怪发出 `SceneCastSkillCsReq`，`GetSceneMapInfo` / `GetCurChallenge` 顺序与官方 #605-#609 一致（以前所有模式都是 0 cast）。失败点换了位置：下半**战斗本身**进场动画后黑屏 9 s、客户端自己判 WON（`battle stats turns=0`；同一套代码上半是 `turns=2 cost=17s`）。对齐官方 `SceneCastSkillScRsp`（#563/#658）补三处：`SceneMonsterWave.battle_stage_id = 本半场 stage`、每个怪一条空 `ItemList`（`NMGIABHIJJB`）、`battle_info.FGOAKDCBIOE` 空子消息（`EDCFKACPFNB`）。另修 `scripts/game_test.ps1` 的 `advance` 事件规则（漏了 `official`，上一轮报告把「阶段切换」误判成未发生、下半 cast 计 0）。240 单测 + check.ps1 绿；真机待复验，失败判据与仍存差异写进 [抓包方案 §11](docs/notes/2026-11-12-正式服抓包方案.md)。
  依据：`logs/test/test-20261112-185701.*`（服务端日志 + packets + 截图 14-17）+ 官方抓包 #563/#658 逐字段 diff。

- **混沌回忆换半场改成逐包照抄正式服：新增 `[endgame] advance = "official"` 并设为默认**：按抓包 §8 重写换半场——原地删上半的怪（`refresh_type=3`）→ 换场上角色实体（删旧 id + 加新 id）→ 3×`SyncLineupNotify` → `ChallengeLineupNotify` → 用 `group_info` 声明下半的组 → 移动通知，10 个包**全在 `PVEBattleResultScRsp` 之前**；不重建场景、不发 `SyncServerSceneChangeNotify` / `EnterSceneByServerScNotify`。配套 `scene.avatar_base`（场上角色实体基座，换半场 +4）+ `scene.leader_entity_id()`（`SceneInfo.leader_entity_id` / 走位包 / `SceneEntityMoveCsReq` 的队长判定都跟着基座走）、`scene.GROUP_REFRESH_REFRESH`（type 3）、`declared_group_notify(state=…)`（官方 `group_info.state` 留空）、`move_notify(entity_id=…)`（官方不带 entity_id）。故意不照抄的两处（挑战 buff 包、官方实体 id）写在 [§11](docs/notes/2026-11-12-正式服抓包方案.md)。**240 单测 + 回放（`advance="official"` 逐包断言）绿，真机未验**；验证不通过先改回 `advance = "sync"`。
  依据：docs/notes/2026-11-12-正式服抓包方案 §8/§9/§11 + `tools/captures/official-20261112-1727`（`dump_frames.py` 解出的 #592-#604 原文）；用户要求直接动手。

- **正式服抓包成功，混沌回忆下半场的官方答案拿到**：2026-11-12 17:27 用 CN 4.7.0 客户端真机抓了一轮混沌回忆第 1 层，843 帧 / 381 条命令全解开。结论：**下半是真打的（`stt.turns=5`、cost 33.3s）**，官方**不发** `EnterChallengeNextPhaseCsReq`、客户端也**不重发** `StartChallengeCsReq`；换半场是**原地换实体组**（删上半怪 group 9 → 删/加 4 个角色实体 → 4 条 `SyncLineupNotify` → `ChallengeLineupNotify` → 用 `group_info` 声明 group 10），**全程不重建场景**，且 `PVEBattleResultScRsp` **最后**才发。完整包序与"与现实现的 7 处差异"写进 [docs/notes/2026-11-12-正式服抓包方案](docs/notes/2026-11-12-正式服抓包方案.md) §8/§9。
  依据：`E:\LCXS\tools\captures\official-20261112-1727`（capture.bin / raw.jsonl / challenge.md）；首版因为漏了 EC2B `KeyScramble` 一帧没解开，已用 `redecode.py` 离线重解并补上回归用例。

- **正式服抓包链路做完并离线验证（下一步的数据来源）**：新增 `E:\LCXS\tools\official\`：`mirror.py`（firefly 反代目标，透传官方登录链、只改写 `query_gateway` 的 ip/port 指向本地中继）、`relay.py`（UDP 中继 + 用 EC2B/ `secret_key_seed` 实时解密）、`report.py`、`run.ps1`、`hsrwire.py`。MT19937-64 与 EC2B 派生（含 `KeyScramble`）同 DanhengServer 原实现用 .NET 逐值对齐，端到端用例用「假官方服加密会话」验证到 `capture.bin` 可被 `hsr.tools.decode` 直读（14 用例全绿）。配套 `redecode.py`（从 `raw.jsonl` 离线重解；密钥切换用"哪把钥匙能让下一帧解析"投票，绕开 `secret_key_seed` 字段号漂移）、`analyze_challenge.py`（挑战窗口包序 + protobuf 解码）、`preflight.ps1`/`run.ps1`（端口/系统代理/Clash TUN 预检，退出还原 `ProxyEnable`）。方案、判据与官方包序见 [docs/notes/2026-11-12-正式服抓包方案](docs/notes/2026-11-12-正式服抓包方案.md)。工具链与抓包原始数据自 2026-11-12 起在 `E:\LCXS\tools` 单独 git 版本化。
  依据：DanhengServer `Common/Util/Security/*` + 2026-11-12 官方 `query_gateway` 实测；用户决定用正式服做参考。

- **混沌回忆下半卡进场动画排查（三路径对照 + 参考源码核对）**：真机坐实「上半 turns=1 / 下半 turns=0」，探针直推上下半都 turns=0、`advance=client` 客户端重打上半，逐条排除换半场方式 / 数据 / 包时序（对齐 LunarCore 时序也无效，已回滚）；核对 LunarCore `advanceStage`（纯服务端推、无 `EnterChallengeNextPhase` handler）与 pearl-sr `FirstNode`（靠 GM 命令手动切节点）后，定位根因为「客户端只在主动发 StartChallenge 时建立完整战斗状态，老项目方案在 4.7.0 不成立」。结论写入 [docs/notes/2026-11-12-混沌回忆下半进场动画排查](docs/notes/2026-11-12-混沌回忆下半进场动画排查.md)，下一步是 `EnterChallengeNextPhaseCsReq` 或抓正式服。文档数字 231→238 单测（README / docs/README §3 / guide/02 / guide/03）。
  依据：2026-11-12 真机日志 `logs/test/probe-20261112*.server.log` + LunarCore/pearl-sr 源码，用户要求先写文档再定下一步。

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

