# hsr-local

> **状态：已归档（2026-11-14）** —— 项目已停止开发并冻结，不再接受 issue / PR。
> 为什么停、最终做到哪、没做完什么、怎么重新跑起来，见
> [docs/archive/00-项目归档说明](docs/archive/00-项目归档说明.md)。
> 归档时只做了文档整理和一处配置默认值修正（`[features].local_cdn` 改回 `false`，让
> `check.ps1` 开箱可跑），业务代码保持 2026-11-13 收工状态。

Python 写的本地单人《崩坏：星穹铁道》私服，默认目标客户端 **4.7.51**（`OSBETAWin4.7.51`，build
`17100438`），同时保留 **4.6.54**（`OSBETAWin4.6.54`，build `16927089`）与 **4.7.0 CN**
（`CNPRODWin4.7.0`，build `17108910`）两个 profile。纯 Python UDP+KCP 直连，不使用 TCP shim、
客户端补丁或密码门。

> **免责声明**：本项目仅供学习、研究和本地调试使用，与 miHoYo / HoYoverse 无关。
> 请使用自己合法拥有的游戏客户端，并自行确认符合当地法律与游戏条款。

> **文档入口**：[docs/README.md](docs/README.md)
> —— 阅读路径、文档地图、状态图例、关键数字和维护约定都在这里。
>
> **接手先读**：[docs/guide/03-交接与排障.md](docs/guide/03-交接与排障.md)
> —— 已验证事实、踩过的坑、已知限制、下一轮任务。
>
> **变更日志**：[CHANGELOG.md](CHANGELOG.md) —— 每次合入一行，不写进 docs。

## 最终状态（2026-11-13 收工）

| 模块 | 状态 |
|---|---|
| UDP + KCP（`u32_le_token`，28B 头）、握手、帧 | ✅ 4.6.54 真机 |
| HTTP dispatch / SDK JSON / hotfix / 公告关闭 | ✅ |
| 登录（`5/74/46/9`）+ 心跳（`45`） | ✅ |
| 角色 / 遗器 / 光锥（freesr 99/418/87）、背包 | ✅ |
| 阵容 / 领队 / 多命途 / 皮肤 | ✅ 真机验证 |
| 固定场景 20313 + 移动 / 地图数据 / 传送 / section | ✅ |
| 凝滞虚影 + 10 BOSS + 「再来一次」 | ✅ 真机可打、可结算、可连续再战 |
| 秘技点 / 战前 HP / 标记物品 | ✅ |
| 任务 / 教程 / 指南 / ServerPrefs | ✅ 全标记完成 |
| Lua 控制台、freesr 网页热更新 | ✅ |
| 混沌回忆（MOC） | ✅ 15 层闭环；🔁 换半场 `advance=official` 已真机；🟡 下半战斗卡进场动画、缺「物竞天择」关卡组卡 |
| 虚构叙事（PF）/ 末日幻影（AS） | 🟡 协议已实现，待真机 |
| 历战余响 | 🟡 协议链 + 单测，入口待真机 |
| 异相仲裁（Peak）/ Tierce | 🟡 Peak 9 命令 + 2 通知已实现（离线 bot 全链通过，待真机）；Tierce 未实现 |
| 掉落 / 背包结算 | ⏸ 按决定不做 |
| 地图「导航」UI | ⏸ 数据已下发，无法自动验证 |
| 4.7.51（`OSBETAWin4.7.51`，4.8 beta） | ✅ 真机通过：登录 / 跑图 / 换队伍 / 换皮肤 / 进战斗 / 结算（4.8 描述符快照 + 140 条 cmd 表；57 个 handler 待补 id） |

当前规模：**262 单测 / 88 handler（4.6.54 全量，4.7.51 partial 为 31、57 个缺命令号）**，`scripts\check.ps1` 全绿。
数字以 [docs/README §3](docs/README.md#3-关键数字唯一事实源) 为准。

> **4.7.0 CN 已真机通过**：`versions/4.7.0.toml` + 本地 CDN 回放（`features.local_cdn`）+
> `GetArchiveData` 空子消息；2026-11-11 真机确认 登录 → 进 20313 → 凝滞虚影三连战、换队、换皮肤。
> 遗留：4.7 新皮肤「群星乐透」未解锁、深渊未测。结论/证据见
> [docs/reference/05-4.7.0兼容性实测](docs/reference/05-4.7.0兼容性实测.md) §5.1。

> **4.7.51 真机通过（2026-11-13）**：客户端是 **4.8 beta**，cmd id 与字段号整体重排。
> 现在用 `proto/4.7.51/classes.desc`（4,993 消息）+ `field_map.json`（字段改名适配）+
> **140 条 cmd 表**（`cmd_table = "partial"`）跑通登录 / 跑图 / 换队伍 / 换皮肤 / 进战斗 / 结算；
> 进游戏加载从 13 s 降到 4.7 s。还没补的 57 个 handler 会在客户端请求时记 `UNKNOWN_<id>`，
> 反查命令号的方法与教训见
> [docs/reference/06-4.7.51接入](docs/reference/06-4.7.51接入.md) §10/§11 与
> [docs/notes/2026-11-13-4.8协议攻坚与验收](docs/notes/2026-11-13-4.8协议攻坚与验收.md)。

## 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11（`run.ps1` / 真机脚本按 Windows 写；服务端代码本身不依赖 Windows） |
| Python | **3.13+**（本仓库在 3.14 上跑通 262 单测） |
| 客户端 | 4.7.51（`OSBETAWin4.7.51`，默认）、4.6.54（`OSBETAWin4.6.54`）或 4.7.0 CN（`CNPRODWin4.7.0`） |
| 代理 | 能把登录域名转到 `127.0.0.1:21000` 的 HTTPS 代理（例如 firefly-go-proxy） |

## 快速开始

```powershell
git clone https://github.com/lcxs1/hsr-local.git
cd hsr-local

py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 也可以（本机没有 3.13/3.14 时）：uv venv .venv --python 3.14
#   uv pip install --python .venv\Scripts\python.exe -r requirements.txt

# 1) 启动服务端
.\run.ps1                 # 或 .\run.bat / .\.venv\Scripts\python.exe -m hsr
```

```powershell
# 2) 另一个窗口：代理 + 游戏（UAC 点“是”）
#    把 $Client 换成你的客户端目录
$Client = 'X:\HSRSiFu'
Start-Process "$Client\firefly-go-proxy.exe" -ArgumentList `
  '-e', "$Client\StarRail_4.7.51\StarRail.exe", '-p', '9978' -Verb RunAs
```

正常应看到：

```text
  hsr-local · OSBETAWin4.7.51 · profile=4.7.51 · uid=10001
  game   udp://127.0.0.1:23301
  sdk    http://127.0.0.1:21000
```

代理需要把登录域名指到 `127.0.0.1:21000`，系统代理指向 `127.0.0.1:9978`。
停在 HoYoPass 登录窗、一直不连接，通常是代理没有拦截 HTTPS。

首次运行会自动创建 `saves/` 和 `logs/`；uid、角色名、端口、默认队伍都在
[config.toml](config.toml)。4.7.51 的 hotfix 是真实 CDN 条目，默认不用额外配置；4.7.0 CN 则要把
`[paths].client_dir` 指向客户端安装目录、把 `[features].local_cdn` 设为 `true`（本地 CDN 回放），
详见 [docs/reference/05](docs/reference/05-4.7.0兼容性实测.md)。

## 常用命令

```powershell
# 全量回归：262 单测 + 离线核心链 + 挑战上下半链 + proto 检查 + ruff + 文档检查
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1

# 真机测试：起服务端/代理/游戏、截图、出报告
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1

# 异相仲裁（Peak）真机：同上，只收 Peak 证据，跑完自动判定
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\peak_test.ps1

# 重启客户端（结束 → 起代理 → 等登录）：代理提权时 agent 也能自己跑
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restart_client.ps1

# 只跑单测
.\.venv\Scripts\python.exe -m unittest discover -s tests

# 严格启动：freesr/队伍/关卡/场景 id 有问题直接退出
.\.venv\Scripts\python.exe -m hsr --strict

# 离线核心链路（不启动客户端）
.\.venv\Scripts\python.exe scripts\replay_flows.py

# 无客户端 e2e：起一次性服务端，用真实 UDP/KCP 跑登录→战斗（4.6.54 跑混沌回忆全链）
.\.venv\Scripts\python.exe scripts\bot_smoke.py
.\.venv\Scripts\python.exe scripts\bot_smoke.py --profile 4.6.54 --challenge

# 解码冻结的 4.6.54 抓包（抓包文件不在仓库内）
python -m hsr.tools.decode --capture <抓包目录>\capture.bin --list
```

## 目录结构

```text
hsr/            服务端代码
  net/          UDP / KCP / 握手 / 帧 / 会话
  dispatch/     HTTP 登录链 / hotfix / 本地 CDN
  game/         handlers + services（角色、场景、战斗、终局）
  protocol/     proto 注册、编解码、生成代码
  tools/        解码 / 探针 / Lua 控制台
proto/          StarRail.proto、CmdId.json、描述符
data/           静态数据（角色、关卡、终局、教程）
versions/       客户端版本 profile（4.7.51 / 4.6.54 / 4.7.0）
tests/          单元测试
scripts/        回归、真机测试、数据导出
docs/           指南 / 设计 / 参考 / 笔记
lua/            Lua 控制台脚本
saves/          运行时存档（不入库）
logs/           运行时日志（不入库）
```

## 文档

| 文档 | 内容 |
|---|---|
| [archive/00-项目归档说明](docs/archive/00-项目归档说明.md) | **归档总结**：为什么停、最终能力、没做完的事、最小复现、时间线 |
| [docs/README.md](docs/README.md) | 文档索引、阅读路径、关键数字、维护约定 |
| [guide/01-运行与配置](docs/guide/01-运行与配置.md) | 启动链路、config 全表、数据/存档、改哪里 |
| [guide/02-测试与回归](docs/guide/02-测试与回归.md) | `game_test.ps1`、手动测试、报告解读 |
| [guide/03-交接与排障](docs/guide/03-交接与排障.md) | 已验证事实、坑、限制、下一轮 |
| [guide/04-无手操测试](docs/guide/04-无手操测试.md) | 无客户端 bot、真机驱动、抓包回放 |
| [design/01-网络与登录](docs/design/01-网络与登录.md) | HTTP 登录链、握手、KCP、帧、proto/cmd |
| [design/02-玩法与数据](docs/design/02-玩法与数据.md) | 角色/阵容/场景/战斗/挑战、freesr、存档 |
| [design/03-终局玩法](docs/design/03-终局玩法.md) | MOC/PF/AS/Peak/Tierce |
| [reference/01-登录序列与基线](docs/reference/01-登录序列与基线.md) | 首登命令表、黄金基线、升级对照 |
| [reference/02-开发与版本更新](docs/reference/02-开发与版本更新.md) | 包体/数据来源、升级 SOP、工具链 |
| [reference/03-参考工程与索引](docs/reference/03-参考工程与索引.md) | 参考工程评估、可移植清单、代码索引 |
| [reference/04-特性对照](docs/reference/04-特性对照.md) | 三套参考实现 + 本项目 MVP 规划对比 |
| [reference/05-4.7.0兼容性实测](docs/reference/05-4.7.0兼容性实测.md) | 4.7.0 版本键、协议兼容、资源校验、真机记录 |
| [reference/06-4.7.51接入](docs/reference/06-4.7.51接入.md) | 4.7.51 指纹、改动清单、离线验证、首登/换表步骤 |

历史资料在 [docs/archive/](docs/archive/)，只用于追溯，不作为现状依据。

## 已知限制

- **单客户端 / 单场景**：新握手替换旧会话；普通传送仍留在 20313。
- **混沌回忆下半仍黑屏**：换半场已按正式服包序实现并真机通过；下半战斗进场后黑屏，
  客户端约 9 s 后自行结算（`turns=0`）。9 类已排除原因与下一步见
  [docs/notes/2026-11-12-下半黑屏收工快照](docs/notes/2026-11-12-下半黑屏收工快照.md)。
- **忘却之庭右侧缺「物竞天择」关卡组卡**；虚构叙事/末日幻影锁定且点开空白；丹恒腾荒技能释放失败。
  排查方向见 [docs/design/03 §6](docs/design/03-终局玩法.md) 与 [docs/guide/03 §5](docs/guide/03-交接与排障.md)。
- **地图导航 UI**：数据已下发，但客户端管理员进程无法自动点开，未真机确认。
- **掉落 / 背包结算**：明确不做。
- **PF/AS**：协议已实现，待真机；**Peak** 协议链已实现（离线 bot 全链通过、待真机），**Tierce** 未实现；网页 `battle_config` 未接线。

完整清单见 [docs/guide/03-交接与排障.md](docs/guide/03-交接与排障.md) §4。

## 参与

> 项目已归档，不再接受 PR / issue；下面的约定只留作记录。

- 提交前跑 `scripts\check.ps1`（单测 + proto + 回放 + 文档 + ruff）。
- 文档里的 handler/单测/数据条数只在 [docs/README §3](docs/README.md#3-关键数字唯一事实源) 维护。
- 真机验证结论请带日期和 `logs/test/<run>.report.md` 报告名。

## License

GPL-3.0-only，见 [LICENSE](LICENSE)。
