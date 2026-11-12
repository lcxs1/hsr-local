# docs · 文档索引

> **状态**：现行（索引本身）　**适用版本**：4.6.54（build 16927089）/ 4.7.0（build 17108910）　**最后修订**：2026-11-12

`hsr-local` 的全部文档都在本目录。本页只做四件事：**告诉你该读哪篇、哪篇可信、数字以谁为准、改文档守什么规矩**。

项目入口与快速开始见仓库根目录 [README.md](../README.md)。

---

## 1. 状态图例

| 标记 | 含义 | 可信度 |
|---|---|---|
| **现行** | 描述当前代码/流程，随代码一起维护 | 以此为准 |
| **参考** | 协议/数据/参考工程的背景知识，不常变，但可能滞后于最新代码 | 可用，关键处回查代码 |
| **计划** | 尚未完成或等待外部条件（新客户端、真机验证） | 只是计划，不是现状 |
| **已归档** | 历史记录/实验过程，只用于追溯“当时为什么这么改” | 不要当现状依据 |

判断现状只看三个地方：本页 §3 的关键数字、[guide/03](guide/03-交接与排障.md)、代码本身。
任何文档里的数字若与本页 §3 不一致，以本页为准。

---

## 2. 文档地图

### 2.0 接手先读（当前正在处理的那件事）

| 文档 | 状态 | 用途 |
|---|---|---|
| [notes/2026-11-11-混沌回忆状态快照](notes/2026-11-11-混沌回忆状态快照.md) | 现行·快照 | **混沌回忆** 交接总览：今晚修了什么、`advance=sync` 换半场为什么现在能用、剩下的「下半战斗卡进场动画」已排除项与下一步探针、日志/截图判据、其它搁置项。上下文压缩/换人接手从这一篇看起 |

### 2.1 入口与实操

| 文档 | 状态 | 用途 |
|---|---|---|
| [../README.md](../README.md) | 现行 | 项目简介、状态表、快速开始、常用命令 |
| [guide/01-运行与配置](guide/01-运行与配置.md) | 现行 | 启动链路、`config.toml` 全表、数据/存档、目录结构、「改哪里」 |
| [guide/02-测试与回归](guide/02-测试与回归.md) | 现行 | `game_test.ps1`、手动测试项、报告怎么读、常见问题 |
| [guide/03-交接与排障](guide/03-交接与排障.md) | 现行 | 已验证事实与证据、踩过的坑、已知限制、下一轮任务 |

### 2.2 实现原理

| 文档 | 状态 | 用途 |
|---|---|---|
| [design/01-网络与登录](design/01-网络与登录.md) | 参考 | HTTP 登录链、20B 握手、KCP、帧、心跳、proto/CmdId、版本策略 |
| [design/02-玩法与数据](design/02-玩法与数据.md) | 参考 | 首登状态机、角色/命途/阵容/场景/战斗/挑战、freesr、存档、数据表 |
| [design/03-终局玩法](design/03-终局玩法.md) | 现行 | 混沌回忆/虚构叙事/末日幻影/异相仲裁的实现进度与遗留下来项 |
| [design/04-混沌回忆模拟器口径](design/04-混沌回忆模拟器口径.md) | 计划 | 选任意层、上下半双队、结算的改造计划（含参考工程对照与探针任务） |

### 2.3 参考资料

| 文档 | 状态 | 用途 |
|---|---|---|
| [reference/01-登录序列与基线](reference/01-登录序列与基线.md) | 参考 | 4.6.54 首登真实命令表、黄金基线、升级时怎么对照 |
| [reference/02-开发与版本更新](reference/02-开发与版本更新.md) | 参考·计划 | 包体/数据从哪来、版本更新要重做什么、升级 SOP、工具链 |
| [reference/03-参考工程与索引](reference/03-参考工程与索引.md) | 参考 | 七套参考工程的定位/评估、可移植清单、关键文件索引 |
| [reference/04-特性对照](reference/04-特性对照.md) | 参考 | 三套参考实现（echium/pearl/ppSR）+ 本项目 MVP 规划的逐项对比 |
| [reference/05-4.7.0兼容性实测](reference/05-4.7.0兼容性实测.md) | 参考（已落地 + 真机） | 4.7.0 客户端版本键、协议兼容实测、资源校验绕行、接入与真机记录 |

### 2.4 归档

| 文档 | 为什么归档 |
|---|---|
| [archive/01-早期设计蓝图](archive/01-早期设计蓝图.md) | 2026-11-07 的施工图，目录结构/存档方案与最终实现不同 |
| [archive/02-下一步行动清单](archive/02-下一步行动清单.md) | 11-07~11-08 的逐轮记录，含“战斗未实现”等已过时结论 |
| [archive/03-P0弹窗定位记录](archive/03-P0弹窗定位记录.md) | 「位面规则」弹窗的定位过程；根因已解决（指南组 `type=0`） |

归档文件保留原文，只在文首加归档说明；**不要按里面的步骤做日常验证**。

### 2.5 探索笔记（不是事实源）

| 文档 | 内容 |
|---|---|
| [notes/社区做法与提速方案](notes/社区做法与提速方案.md) | 七套参考工程的做法对比、可移植做法、30 天三条主线与可批准工作包 |
| [notes/规则修订-2026-11-11](notes/规则修订-2026-11-11.md) | v1 协作规则为什么拖慢开发：证据、七条阻碍、v1→v2 改动映射、未决事项 |

笔记可以粗糙、可以自我推翻，**不当事实源**；结论回填现行文档后才算数。

---

## 3. 关键数字（唯一事实源）

最后核对：**2026-11-12**，由 `scripts/check.ps1` / 实际运行得出。

| 指标 | 值 | 怎么来的 |
|---|---|---|
| 客户端版本（默认） | 4.6.54 / `OSBETAWin4.6.54` / build `16927089` | `versions/4.6.54.toml` |
| 客户端版本（4.7.0） | `CNPRODWin4.7.0` / build `17108910` | `versions/4.7.0.toml`，用 `--profile 4.7.0` 切换 |
| handler | **78** | `hsr/game/handlers/*.py` 的 `router.add(...)` |
| 单测 | **231** | `.venv\Scripts\python.exe -m unittest discover -s tests` |
| freesr 角色/遗器/光锥 | 99 / 418 / 87 | `data/freesr-data.json` |
| 教程/指南组/子指南 | 1269 / 864 / 1509 | `data/tutorials.json` |
| 主线/任务/活动 | 2205 / 6018 / 347 | `data/progress.json` |
| 终局挑战/关卡/场景/入口 | 843 / 1538 / 22 / 931 | `data/endgame.json` |
| 忘却之庭大厅 entry / 道具 | 17 / 209（入口 100000104 有 13 个） | `data/endgame.json.lobby`（`scripts/export_endgame.py`） |
| 凝滞虚影关卡 | 82 | `data/stages.json` |
| 皮肤 | 8 | `data/skins.json` |
| ServerPrefs | 16 | `saves/server_prefs.json`（客户端会重写，数字会变） |

> 新增/修改 handler 或测试后，只改这张表和根 README 的状态；其它文档写“见 docs/README §3”，
> **不要各写一份**。

---

## 4. 按角色的阅读路径

### 4.1 第一次接手

1. [README](../README.md) —— 项目是什么、怎么跑；
2. [guide/03-交接与排障](guide/03-交接与排障.md) —— 哪些已验证、坑在哪、下一步做什么；
3. [guide/01-运行与配置](guide/01-运行与配置.md) —— 配置、数据、目录、改哪里。

### 4.2 要改某个玩法

1. [design/02-玩法与数据](design/02-玩法与数据.md) 找对应章节；
2. [guide/01-运行与配置](guide/01-运行与配置.md) §8「改哪里」定位文件；
3. 改完跑 `scripts\check.ps1`，真机按 [guide/02](guide/02-测试与回归.md) 回归。

### 4.3 要真机测试

1. [guide/02-测试与回归](guide/02-测试与回归.md)；
2. 报告看 `logs/test/<run>.report.md`；
3. 历史证据在 `logs/test/`，不要在 `logs/exp/`（旧实验目录）里找。

### 4.4 要升客户端版本

1. [reference/02-开发与版本更新](reference/02-开发与版本更新.md)；
2. [reference/01-登录序列与基线](reference/01-登录序列与基线.md) 的对照方法；
3. [reference/05-4.7.0兼容性实测](reference/05-4.7.0兼容性实测.md) —— 4.7.0 已经实测过的结论与接入清单；
4. [design/01-网络与登录](design/01-网络与登录.md) §5「cmd id 不能跨版本复用」。

### 4.5 要查协议/字段

1. [design/02-玩法与数据](design/02-玩法与数据.md) 具体玩法章节；
2. [design/01-网络与登录](design/01-网络与登录.md) 命名坑、动态解码；
3. 原始 `proto/StarRail.proto`、`proto/CmdId.json`、`proto/hsr.desc`；
4. 抓包解码：`python -m hsr.tools.decode --capture <bin> --list`。

---

## 5. 维护约定

### 5.1 每篇文档的头部

非归档文档统一以一行元信息开头（紧跟一级标题）：

```markdown
> **状态**：现行 / 参考 / 计划　**适用版本**：4.6.54（build 16927089）　**最后修订**：YYYY-MM-DD
```

- **最后修订**写“内容被核对/改动”的日期，不是文件被 touch 的日期；
- 状态变化（如计划转现行）要同时更新本页 §2；
- 归档文档用：`> **状态**：已归档（历史记录，勿作为现状依据）　**归档日期**：YYYY-MM-DD`。

### 5.2 数字不要抄

- handler / 单测 / 数据条数只在**本页 §3** 维护（根 README 的状态摘要除外）；
- 其它文档需要时写“见 docs/README §3”；
- 真机验证结论必须带**日期 + 报告文件名**（`logs/test/<run>.report.md`），不写“已测试”这种无证据的话。

### 5.3 路径与链接

- 文档内链接一律**相对当前文件**：同目录用 `./`，上级用 `../`；
  项目内文件从 `docs/` 出发是 `../../hsr/...`、`../../data/...`；
  参考工程是 `../../echium-server-main/...`；
- 会变的绝对路径（客户端、代理、`E:\LCXS\tools`）放在**代码块或表格**里，
  不要做成 markdown 链接，避免迁移后失效；
- 不要写旧项目路径（`E:\LCXS` + 旧目录名）或旧文档目录。

### 5.4 编号与归档

- 每个子目录内编号从 `01` 起，**同一目录内不重复**；新增长文按目录顺序追加；
- 被新文档取代的旧文移到 `archive/`，文首加归档说明，并更新本页 §2.4；
- 不要复用已归档的编号。

### 5.5 提交前自检

```powershell
cd E:\LCXS\run\hsr-local
.\.venv\Scripts\python.exe scripts\check_docs.py
```

检查项：失效相对链接、旧绝对路径、重复编号、缺失元信息、归档文档是否被当成现行引用。
该脚本已接入 `scripts\check.ps1`。

---

## 6. 修订记录

### 2026-11-10 目录重建

| 动作 | 内容 |
|---|---|
| 重建 | `docs/` 改为 `guide/`、`design/`、`reference/`、`archive/` 四个主题目录 |
| 合并 | 01+02→design/01；03+04→design/02；08+15→reference/01；17+18→reference/02；19+09→reference/03 |
| 保留 | 原 07 特性对照表 → reference/04，删除已弃用工程的列 |
| 精简 | 根 README 只留简介/状态/快速开始/常用命令/文档入口；配置细节移入 guide/01 |
| 去重 | 删除重复的 P0 弹窗、freesr 热更新、战斗实现细节，只保留一处事实源 |
| 修正 | handler=72、tests=160、prefs=16；KCP 已判定 `u32_le_token`；MOC 15 层 |
| 清理 | 删除已弃用的早期学习工程内容（07 删列，其余不再收录） |
| 归档 | 06/10/12 移到 `archive/` 并加归档说明 |

### 2026-11-11 挑战链真机修复

| 动作 | 内容 |
|---|---|
| 修复 | `battle_id` 幂等（复用已激活战斗）+ 结算按本次挑战分配过的 id 集合判定 |
| 修复 | 忘却之庭大厅 plane 10000 数据：`data/endgame.json.lobby`（17 entry / 209 道具），`enter_scene` 按 entry 生成场景组，`SceneMapInfo` 上报真实组与道具 |
| 新增 | 两段式结算：`can_advance`/`advance_stage`，上半打完加载下半 + `ChallengeLineupNotify`，下半打完才 `ChallengeSettleNotify` |
| 数字 | §3 单测 181 → 188；新增大厅 entry/道具一行 |
| 第二轮 | 上下半各用各的队伍（`first_avatars`/`second_avatars`）；同 entry 传送可重入；`LeaveChallenge` 回世界场景（`session.lobby_return`）；单测 188 → 192 |
| 第三/四轮 | 补 `SceneBattleInfo.monster_wave_length`（字段 7 `JAOIOKBBMFC`，客户端 `battleAreaDef` 失败消失）、`challenge_group_list`（109 组）；单测 192 → 193。仍未解决：下半黑屏并自行结算、缺「物竞天择」卡、PF/AS 锁定+点开空白、丹恒腾荒技能（搁置），见 [guide/03 §5](guide/03-交接与排障.md)、[design/03 §6](design/03-终局玩法.md) |
| 证据 | 真机问题记录见 `logs/test/test-20261111-1720*`（本轮报告）、[design/03 §5](design/03-终局玩法.md)、[guide/03 §3](guide/03-交接与排障.md) 坑 25–27 |

### 2026-11-11 4.7.0 真机

| 动作 | 内容 |
|---|---|
| 真机 | `game_test.ps1 -Client 4.7.0`：登录 → 进 20313 → 凝滞虚影三连战（`battle_id=1/2/3` 全 WIN）、换队、换皮肤正常 |
| 修复 | 本地清单分隔符 `..` → CRLF（严格解析失败会弹「任务资源管理」全量下载）；`game_test.ps1` 恢复 UTF-8 BOM |
| 遗留 | 4.7 新皮肤「群星乐透」未解锁（缺 4.7 `AvatarSkin` id）；深渊未测；未实现模块的空回包 NRE 噪声 |
| 证据 | `logs/test/test-20261111-124641.report.md` + 截图 `-1/-2/-3.png`；记录进 [reference/05](reference/05-4.7.0兼容性实测.md) §5.1、[guide/03](guide/03-交接与排障.md) §1/§3 |

### 2026-11-11 4.7.0 接入

| 动作 | 内容 |
|---|---|
| 新增 | `versions/4.7.0.toml`（`CNPRODWin4.7.0`，复用 4.6.54 proto 快照） |
| 新增 | 本地 CDN 回放：`hsr/dispatch/local_cdn.py` + `features.local_cdn` + `paths.client_dir` + `/cdn/**` |
| 新增 | 官方网关补 hotfix：`hsr/dispatch/official.py` + `features.hotfix_auto` |
| 新增 | `hsr/game/handlers/archive.py`：`GetArchiveData` / `GetUpdatedArchiveData` 返回空但存在的子消息 |
| 同步 | `data/hotfix.json`（`CNPRODWin4.7.0` 本地条目）、`scripts/game_test.ps1`（`-Client 4.7.0`）、[guide/01](guide/01-运行与配置.md) §3/§4、[design/01](design/01-网络与登录.md) §2.3、[guide/03](guide/03-交接与排障.md) §3/§5、[reference/05](reference/05-4.7.0兼容性实测.md) §5 |
| 修正 | §3 数字：handler=74、tests=181 |

### 2026-11-11 4.7.0 实测

| 动作 | 内容 |
|---|---|
| 新增 | [reference/05-4.7.0兼容性实测](reference/05-4.7.0兼容性实测.md)：版本键、协议兼容、资源校验绕行、接入清单 |
| 同步 | 索引 §2.3、升级阅读路径 §4.4、[reference/02](reference/02-开发与版本更新.md) §9、[design/01](design/01-网络与登录.md) §2.3、[guide/01](guide/01-运行与配置.md) §1、[guide/03](guide/03-交接与排障.md) §3/§5 |


