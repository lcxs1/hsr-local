# proto/4.7.51 —— 4.8 beta 协议快照

来源：`E:\SiFu\HSRSiFu\ppSR_REL_4.7.5X\ppsr.exe`（PyInstaller，Python 3.14）。
2026-11-13 用 PyInstaller 归档读取器取出 `msg.classes_pb2` 的 `FileDescriptorProto`
与 `game.cmdid` 的 `cmd_dict`。

**状态：2026-11-13 18:04 真机首登通过**（cmd 73 → 83 → 583 → 383 → 753 → 1407，角色进入
20313 可跑图，见 [reference/06 §9](../../docs/reference/06-4.7.51接入.md)）。

| 文件 | 内容 |
|---|---|
| `classes.desc` | `FileDescriptorSet`，1 个文件 `classes.proto`：**4,993 消息 / 476 枚举**（4.6.54 是 5,051 / 478） |
| `CmdId.json` | ppSR 实际注册的 **79 条**命令（name → cmd id）；不是全量表，profile 因此标 `cmd_table = "partial"` |
| `field_map.json` | 4.8 的字段/消息名 → handler 沿用的 4.6.54 名字（**只改名，不改字段号**） |

## 为什么没有 `StarRail_pb2.py`

4.6.54 和 4.8 都定义了 `PlayerLoginCsReq` 等同名消息。生成的 pb2 会注册进 protobuf 的
**默认池**，同一个进程 import 两份就会 duplicate symbol。所以 4.8 走
[hsr/protocol/dynpb.py](../../hsr/protocol/dynpb.py)：用 `classes.desc` 在私有
`DescriptorPool` 里建类，首次访问才建（4,993 个消息的启动开销可忽略）。

`Registry(proto_dir)` 看到 `classes.desc` 就走这条路，否则回落到生成模块；
handler 统一 `from hsr.protocol import pb`，由 `Session._run` 的 contextvar 决定用哪份。

## `field_map.json` 的方向

键是 4.8 快照里的**现有名字**，值是 handler 用的名字：

```json
{
  "LineupInfo": { "lineup_type": "extra_lineup_type" },
  "SceneActorInfo": { "character_avatar_type": "avatar_type" },
  "SceneInfo": { "curr_player_world_id": "world_id" },
  "__messages__": { "MazeGroup": "MapInfoGroup" }
}
```

加载时把 `lineup_type` 改名成 `extra_lineup_type`，字段号仍是 4.8 的，
所以编码出去的线格式不变，handler 一行不用改。`__messages__` 是消息类型改名。
加新映射时注意目标名不能和该消息已有字段重名（dynpb 会直接报错）。

## 还缺什么

- 只覆盖登录/场景/背包/阵容/皮肤等核心链（23 个 handler）；另外 56 个 handler 还没有命令号，
  客户端发到时服务端记 `UNKNOWN_<id>`。补法见 reference/06 §10。
- `GetSceneMapInfo` 的 4.8 结构变化最大（`group_list` 换了消息类型、宝箱字段重排），
  已用消息别名 + 字段改名构造，但地图正确性未真机验证。
