# hsr-local

Python 写的本地单人《崩坏：星穹铁道》私服，目标客户端 **4.6.54**
（`OSBETAWin4.6.54`，build `16927089`）与 **4.7.0 CN**（`CNPRODWin4.7.0`，build `17108910`，
`--profile 4.7.0`）。纯 Python UDP+KCP 直连，不使用 TCP shim、客户端补丁或密码门。

> **文档入口**：[docs/README.md](docs/README.md)
> —— 阅读路径、文档地图、状态图例、关键数字和维护约定都在这里。
>
> **接手先读**：[docs/guide/03-交接与排障.md](docs/guide/03-交接与排障.md)
> —— 已验证事实、踩过的坑、已知限制、下一轮任务。
>
> **协作规范**：[AGENTS.md](AGENTS.md) —— 风险分级（R0/R1/R2）、验证分级（V0–V3）、完成分“合入/可上线”、快车道与 5 条不变式。
>
> **变更日志**：[CHANGELOG.md](CHANGELOG.md) —— 每次合入一行，不写进 docs。

## 当前状态（2026-11-11）

| 模块 | 状态 |
|---|---|
| UDP + KCP（`u32_le_token`，28B 头）、握手、帧 | ✅ 4.6.54 真机 |
| HTTP dispatch / SDK JSON / hotfix / 公告关闭 | ✅ |
| 登录（`5/74/46/9`）+ 心跳（`45`） | ✅ |
| 角色 / 光锥 / 遗器（freesr 99/418/87）、背包 | ✅ |
| 阵容 / 领队 / 多命途 / 皮肤 | ✅ 真机验证 |
| 固定场景 20313 + 移动 / 地图数据 / 传送 / section | ✅ |
| 凝滞虚影 + 10 BOSS + 「再来一次」 | ✅ 真机可打、可结算、可连续再战 |
| 秘技点 / 战前 HP / 标记物品 | ✅ |
| 任务 / 教程 / 指南 / ServerPrefs | ✅ 全标记完成 |
| Lua 控制台、freesr 网页热更新 | ✅ |
| 混沌回忆（MOC） | ✅ 15 层闭环；🟡 下半战斗黑屏、忘却之庭缺「物竞天择」关卡组卡 |
| 虚构叙事（PF）/ 末日幻影（AS） | 🟡 协议已实现，待真机 |
| 历战余响 | 🟡 协议链 + 单测，入口待真机 |
| 异相仲裁（Peak）/ Tierce | ⬜ 未实现，数据已就绪 |
| 掉落 / 背包结算 | ⏸ 按决定不做 |
| 地图「导航」UI | ⏸ 数据已下发，无法自动验证 |

当前规模：**193 单测 / 74 handler**，`scripts\check.ps1` 全绿。
数字以 [docs/README §3](docs/README.md#3-关键数字唯一事实源) 为准。

> **4.7.0 CN 已真机通过**：`versions/4.7.0.toml` + 本地 CDN 回放（`features.local_cdn`）+
> `GetArchiveData` 空子消息；2026-11-11 真机确认 登录 → 进 20313 → 凝滞虚影三连战、换队、换皮肤。
> 遗留：4.7 新皮肤「群星乐透」未解锁、深渊未测。结论/证据见
> [docs/reference/05-4.7.0兼容性实测](docs/reference/05-4.7.0兼容性实测.md) §5.1。

## 快速开始

```powershell
cd E:\LCXS\run\hsr-local

# 1) 启动服务端
.\run.ps1                 # 或 .\run.bat / .\.venv\Scripts\python.exe -m hsr

# 2) 另一个窗口：代理 + 游戏（UAC 点“是”）
cd E:\SiFu\HSRSiFu
Start-Process .\firefly-go-proxy.exe -ArgumentList '-e','E:\SiFu\HSRSiFu\StarRail_4.6.54\StarRail.exe','-p','9978' -Verb RunAs
```

正常应看到：

```text
  hsr-local · OSBETAWin4.6.54 · profile=4.6.54 · uid=10001
  game   udp://127.0.0.1:23301
  sdk    http://127.0.0.1:21000
```

代理需要把登录域名指到 `127.0.0.1:21000`，系统代理指向 `127.0.0.1:9978`。
停在 HoYoPass 登录窗、一直不连接，通常是代理没有拦截 HTTPS。

## 常用命令

```powershell
cd E:\LCXS\run\hsr-local

# 全量回归：193 单测 + 离线 5 链路 + proto 检查 + ruff + 文档检查
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1

# 真机测试：起服务端/代理/游戏、截图、出报告
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1

# 只跑单测
.\.venv\Scripts\python.exe -m unittest discover -s tests

# 严格启动：freesr/队伍/关卡/场景 id 有问题直接退出
.\.venv\Scripts\python.exe -m hsr --strict

# 离线核心链路（不启动客户端）
.\.venv\Scripts\python.exe scripts\replay_flows.py

# 解码冻结的 4.6.54 抓包
python -m hsr.tools.decode --capture E:\LCXS\tools\golden\4.6.54\capture.bin --list
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/README.md](docs/README.md) | 文档索引、阅读路径、关键数字、维护约定 |
| [guide/01-运行与配置](docs/guide/01-运行与配置.md) | 启动链路、config 全表、数据/存档、改哪里 |
| [guide/02-测试与回归](docs/guide/02-测试与回归.md) | `game_test.ps1`、手动测试、报告解读 |
| [guide/03-交接与排障](docs/guide/03-交接与排障.md) | 已验证事实、坑、限制、下一轮 |
| [design/01-网络与登录](docs/design/01-网络与登录.md) | HTTP 登录链、握手、KCP、帧、proto/cmd |
| [design/02-玩法与数据](docs/design/02-玩法与数据.md) | 角色/阵容/场景/战斗/挑战、freesr、存档 |
| [design/03-终局玩法](docs/design/03-终局玩法.md) | MOC/PF/AS/Peak/Tierce |
| [reference/01-登录序列与基线](docs/reference/01-登录序列与基线.md) | 首登命令表、黄金基线、升级对照 |
| [reference/02-开发与版本更新](docs/reference/02-开发与版本更新.md) | 包体/数据来源、升级 SOP、工具链 |
| [reference/03-参考工程与索引](docs/reference/03-参考工程与索引.md) | 参考工程评估、可移植清单、代码索引 |
| [reference/04-特性对照](docs/reference/04-特性对照.md) | 三套参考实现 + 本项目 MVP 规划对比 |

历史资料在 [docs/archive/](docs/archive/)，只用于追溯，不作为现状依据。

## 已知限制

- **单客户端 / 单场景**：新握手替换旧会话；普通传送仍留在 20313。
- **挑战链未收尾（2026-11-11）**：下半进战斗黑屏（客户端约 16 s 后自行结算）；
  忘却之庭右侧缺「物竞天择」关卡组卡；虚构叙事/末日幻影锁定且点开空白；丹恒腾荒技能释放失败。
  排查方向见 [docs/design/03 §6](docs/design/03-终局玩法.md) 与 [docs/guide/03 §5](docs/guide/03-交接与排障.md)。
- **地图导航 UI**：数据已下发，但客户端管理员进程无法自动点开，未真机确认。
- **掉落 / 背包结算**：明确不做。
- **PF/AS**：协议已实现，待真机；**Peak/Tierce** 未实现；网页 `battle_config` 未接线。

完整清单见 [docs/guide/03-交接与排障.md](docs/guide/03-交接与排障.md) §4。
