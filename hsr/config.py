"""Configuration loading.

`config.toml` is parsed with the standard library (tomllib) and validated by pydantic.
All relative paths are resolved against the project root (the directory that contains
`config.toml`), never against the current working directory.
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("hsr.config")


class ServerSettings(BaseModel):
    listen: str = "127.0.0.1"
    game_port: int = 23301
    # Where the UDP socket actually binds.  Defaults to `listen`.  Set it to a
    # different loopback address (e.g. 127.0.0.2) to put tools/tap.py on
    # `listen:game_port` while the game server keeps the advertised address.
    game_bind_host: str | None = None
    http_port: int = 21000
    # Which versions/<name>.toml to serve.  The profile is authoritative for
    # the client build; the two fields below are legacy fallbacks used by tests
    # and by tools that construct ServerSettings() directly.
    profile: str = "4.6.54"
    version: str = "OSBETAWin4.6.54"
    client_build: int = 16927089
    single_client: bool = True
    kcp_conv_mode: Literal["u32_le_token", "u64_be"] = "u32_le_token"
    kcp_mtu: int = 1400
    kcp_sndwnd: int = 256
    kcp_rcvwnd: int = 256
    heartbeat_timeout: float = 15.0
    unknown_mode: Literal["empty_rsp", "silent", "error"] = "empty_rsp"
    log_level: str = "INFO"


class PathSettings(BaseModel):
    proto_dir: str = "proto"
    data_dir: str = "data"
    save_dir: str = "saves"
    log_dir: str = "logs"
    lua_dir: str = "lua"
    freesr: str = "data/freesr-data.json"
    hotfix: str = "data/hotfix.json"
    # 客户端安装目录（例如 E:\SiFu\HSRSiFu\StarRail_CN_4.7.0）。只读回放用；
    # 留空表示不做本地 CDN 回放。
    client_dir: str = ""
    prefs: str = "saves/server_prefs.json"
    save: str = "saves/player.json"
    tutorials: str = "data/tutorials.json"
    progress: str = "data/progress.json"
    battle: str = "data/battle.json"
    stages: str = "data/stages.json"
    endgame: str = "data/endgame.json"
    skins: str = "data/skins.json"

    def resolve(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (ROOT / p)


class EndgameSettings(BaseModel):
    """终局玩法口径（A 模拟器口径，默认）。

    ``moc_group_id``: 混沌回忆用哪一组（一组 = 一期 12/10 层）。
    ``0`` = 按 ``data/vendor/srtools-*/endgame_index.json`` 的 ``BeginTime/EndTime``
    自动选“当前赛季”；写死数字则强制用那一组。

    ``unlock_all``: true = 所有层按满星下发（客户端视为可选、可重打，pearl-sr 做法）；
    false = 没通关历史就发 0 星（会锁住后面的层，等 M3 历史落库后再用）。

    ``group_list`` / ``send_max_level`` 只是 M0 真机探针开关（一次只改一个），
    用来验证“忘却之庭第三张卡”到底吃什么数据；默认保持现状。
    """

    moc_group_id: int = 0
    unlock_all: bool = True
    # 一个阶段（上半 / 星启节点）打完怎么进下一个（真机 A/B 用，一次只改一个）：
    #   declare = 原地声明下一个迷宫组：ChallengeLineupNotify + 角色组刷新 → 结果包
    #             → SceneGroupRefreshScNotify(GroupRefreshInfo.group_info=整组)；
    #             不推场景、不触发客户端重载 stage（4.7 客户端认这条声明路径）
    #   sync    = 参考顺序（LunarCore `advanceStage`）：ChallengeLineupNotify →
    #             SyncServerSceneChangeNotify + SyncLineupNotify +
    #             EnterSceneByServerScNotify（整份新 SceneInfo；客户端会重载 stage）
    #   inplace = 同场景原地换组（逐实体 patch，4.7 会在客户端 NRE，仅留作 A/B）
    #   scene   = 不发 SyncServerSceneChangeNotify，直接推新 SceneInfo（A/B）
    #   client  = 只回战斗结果，等客户端自己发 StartChallenge（pearl 流程）
    advance: Literal["declare", "sync", "inplace", "scene", "client"] = "sync"
    # 忘却之庭大厅报哪些场景组（客户端按“已加载组”创建地图 NPC）：
    #   all      = 全部道具（含车厢装饰）
    #   crystals = 只报可交互展台（prop_id >= 100000）
    #   none     = 一个都不报
    # 4.6.54 的 MapNPC:227-400001 ConfigEntityPath 为空，一旦被创建就在
    # AdventurePhase._EnterStep2 里 NRE，传送进度条卡死；装饰组带这个 NPC。
    lobby_props: Literal["all", "crystals", "none"] = "all"
    # 指导「传送」进忘却之庭时用哪个入口（0 = 用客户端请求的 100000104）。
    # 参考实现里终局大厅用的是 100000352 / floor 10000003（观景车厢）：
    # DanhengServer CHALLENGE_PEAK_ENTRANCE、LunarCore CHALLENGE_PEAK_ENTRANCE
    # 都是这个值，pearl-sr 离开 Peak 时也回这里。4.6.54 的 100000104 里有
    # ConfigEntityPath 为空的 MapNPC:227-400001，会卡死传送，换一间车厢试试。
    lobby_entry: int = 0
    # 「传送」进忘却之庭大厅（plane 10000）要不要真的切场景。
    #   两个客户端都会在加载 floor 10000000 时挂在配置残缺的地图 NPC 上
    #   （MapNPC:227-400001 → MapNpcDef.get_ConfigEntityPath NRE → SDK 弹框 → 会话关闭），
    #   但不是每次都挂：2026-11-11 21:02 真机成功过一次，21:05 就是从大厅面板开的混沌回忆。
    #   scene  = 照常切（默认；卡了就重启客户端重试，或换 lobby_entry 试另一个 floor）
    #   ignore = 不切，只重发当前场景（不会卡，但大厅 UI / 水晶也进不去）
    lobby_teleport: Literal["scene", "ignore"] = "scene"

    @field_validator("lobby_teleport", mode="before")
    @classmethod
    def _legacy_lobby_teleport(cls, value):
        """``auto`` was a short-lived default (4.7.0 ignore); map it back."""
        return "scene" if str(value).lower() == "auto" else str(value).lower()

    # 阶段切换时要不要发“卸载旧迷宫组”。
    # 4.7.0 真机（2026-11-11 22:56）：用 GroupRefreshInfo(refresh_type=LOADED,
    # 逐实体 delete) 发卸载 → 客户端当成“激活这个组”，报
    # `Reload ServerGroup Error, Already Exists ... ServerGroupID: 9` 并 NRE，
    # 之后卡在黑屏/近战、再也不发 SceneCastSkill。所以默认 **不卸载**，只加载新组。
    # true = 发 refresh_type=UNLOAD 的整组卸载（未真机验证）。
    unload_old_group: bool = False
    # 登录后多少秒内点「传送」要压着场景推送，等客户端自己的场景加载态就绪再发。
    # 4.7.0 真机三组数据（同版本、同入口）：
    #   登录后 18 s 传送 -> 卡死；113 s 传送 -> 正常；14 s 传送 -> 卡死。
    # 卡死栈固定是 `MapNPC:227-400001 / MapNpcDef.get_ConfigEntityPath` NRE，
    # 属客户端加载期的竞态，服务端只能错开时间：EnterSceneScRsp 立刻回，
    # `EnterSceneByServerScNotify` 延迟到登录后这么久再推。0 = 不延迟。
    lobby_grace_seconds: float = 25.0
    group_list: Literal["all", "rewards"] = "all"
    send_max_level: bool = False

    @field_validator("advance", mode="before")
    @classmethod
    def _legacy_advance(cls, value):
        """Old probe values (``refresh``/``none``) still work after the rewrite."""
        legacy = {"refresh": "inplace", "none": "client"}
        return legacy.get(str(value).lower(), str(value).lower())


class PlayerSettings(BaseModel):
    uid: int = 10001
    name: str = "Trailblazer"
    level: int = 67
    world_level: int = 5
    stamina: int = 240
    # 1 = male, 2 = female.  This is the authoritative switch for the
    # Trailblazer model/path list; tb_multi_path is adjusted to match it.
    gender: int = 2
    tb_multi_path: int = 8001
    march_multi_path: int = 1001
    # Used on the first login (no save yet) and when a saved lineup references
    # avatars that no longer exist in a replaced freesr-data.json.
    default_lineup: list[int] = Field(default_factory=list)

    @field_validator("gender")
    @classmethod
    def _gender(cls, value: int) -> int:
        if value not in (1, 2):
            raise ValueError("player.gender must be 1 (male) or 2 (female)")
        return value


class WorldSettings(BaseModel):
    # Optional content pack.  When set, it is loaded first and the fields below
    # override it; this keeps config.toml usable for a quick one-off tweak.
    file: str = ""
    plane_id: int = 20313
    # Explicit topology.  None keeps the old plane_id*1000+1 fallback for
    # hand-written configs, but data/world.json always sets them.
    floor_id: int | None = None
    entry_id: int | None = None
    dimension_id: int = 0
    map_layer: int = 0
    # 0 = generate a random one per session (the client only echoes it back)
    interact_id: int = 0
    # false = always spawn at (x, y, z) below (single-player sandbox default);
    # true  = resume the last walked position from saves/player.json
    persist_position: bool = False
    x: int = 31440
    y: int = 192820
    z: int = 433790
    calyx_group_id: int = 186
    calyx_inst_id: int = 300001
    calyx_entity_id: int = 1337
    calyx_prop_id: int = 808
    # Cocoon id the client sends for this calyx (1014 on plane 20313).
    # config_check requires it to exist in data/battle.json's cocoon_stage_map.
    calyx_cocoon_id: int = 0
    calyx_x: int = 31440
    calyx_y: int = 192820
    calyx_z: int = 433790
    # 这张图上的传送锚点（= MapEntrance id）。地图 UI 用它画锚点列表。
    teleport_ids: list[int] = Field(default_factory=lambda: [2031301, 2031302, 2031303])
    avatar_group_id: int = 0
    leader_entity_id: int = 1
    team_size: int = 4
    # Inclusive [start, end] ranges; flattened into SceneMapInfo.
    lighten_section_ranges: list[list[int]] = Field(
        default_factory=lambda: [[0, 100], [10000, 10050], [20000, 20000], [30000, 30020]]
    )

    @field_validator("team_size")
    @classmethod
    def _team_size(cls, value: int) -> int:
        if not 1 <= value <= 4:
            raise ValueError("team_size must be between 1 and 4")
        return value

    @field_validator("lighten_section_ranges")
    @classmethod
    def _lighten_ranges(cls, value: list[list[int]]) -> list[list[int]]:
        for item in value:
            if len(item) != 2:
                raise ValueError(f"lighten_section_ranges item must be [start, end]: {item}")
        return value


class GameplaySettings(BaseModel):
    """单人便利开关（只影响资源数值，不改协议形态）。

    协议里 ``LineupInfo.mp / max_mp`` 就是队伍共享的**秘技点**（客户端最多显示 5
    个菱形）。客户端在放秘技时自己先扣一点并发 ``SceneCastSkillCostMpCsReq``；
    hsr-local 用 ``SceneCastSkillMpUpdateScNotify``（1448）把权威值推回去。

    * ``unlimited`` —— 不消耗：每次都推 ``mp = max``，可以无限放秘技（默认）。
    * ``refill``    —— 按 ``technique_refill_seconds`` 定时恢复，每个心跳检查一次。
    * ``on_empty``  —— 正常消耗，但扣到 0 时立即回满（不会卡在 0）。
    * ``normal``    —— 真机行为：只扣不回，点数为 0 就不能再放（对照用）。
    """

    technique_points: Literal["unlimited", "refill", "on_empty", "normal"] = "unlimited"
    technique_points_max: int = 5
    technique_refill_seconds: float = 5.0

    @field_validator("technique_points_max")
    @classmethod
    def _mp_max(cls, value: int) -> int:
        if not 1 <= value <= 99:
            raise ValueError("gameplay.technique_points_max must be between 1 and 99")
        return value

    @field_validator("technique_refill_seconds")
    @classmethod
    def _mp_refill(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("gameplay.technique_refill_seconds must be > 0")
        return value


class FeatureSettings(BaseModel):
    send_hotfix: bool = True
    # 本地 CDN 回放（docs/reference/05 §3）：hotfix 里的 LOCAL_CDN 前缀换成
    # http://<listen>:<http_port>/cdn，按文件名回放客户端自己安装里的资源。
    # 只对带 LOCAL_CDN 条目的版本生效，4.6.54 不受影响。需要 [paths].client_dir。
    local_cdn: bool = False
    # 缺 hotfix 条目时向官方网关取一次（默认关闭）。
    hotfix_auto: bool = False
    # 默认直连并绕过本机代理；只有代理环境必然失败时才填中转地址（如社区代理）。
    hotfix_proxy: str = ""
    lua_console: bool = True
    dump_proto: bool = False
    # freesr-data.json 热更新：改装备/角色后不用重启服务端。
    #   freesr_hot_reload  开 = 允许 /srtools 与文件监视覆盖内存里的 roster
    #   freesr_watch       开 = 轮询 data/freesr-data.json 的 mtime/size
    #   freesr_watch_interval  轮询间隔（秒）
    freesr_hot_reload: bool = True
    freesr_watch: bool = True
    freesr_watch_interval: float = 2.0

    @field_validator("freesr_watch_interval")
    @classmethod
    def _watch_interval(cls, value: float) -> float:
        if value < 0.5:
            raise ValueError("features.freesr_watch_interval must be >= 0.5 seconds")
        return value


class ExperimentSettings(BaseModel):
    """P0「位面规则」弹窗的 A/B 开关。

    Every switch defaults to the *old* behaviour (off) so a normal start is
    unchanged.  ``scripts/exp_popup.ps1`` writes a temporary config with one or
    more switches enabled, which makes the real-client tests comparable.
    """

    # Login-state repairs: answer the "who am I" queries with a complete account
    # instead of an empty one (all missions / quests finished).
    popup_mission: bool = False
    popup_quest: bool = False

    # off    = answer with an empty schedule (what the client got before)
    # ended  = list every activity module with a time window that is over
    # active = list every activity module as currently running
    popup_activity: Literal["off", "ended", "active"] = "off"

    # keep   = send an empty PlayerSettingInfo (old behaviour)
    # omit   = do not send player_setting_info at all
    # nested = additionally fill the nested setting sub-messages
    popup_basic_info: Literal["keep", "omit", "nested"] = "keep"

    # SDK ``combo/granter/api/getConfig`` announcement settings.
    # disabled = announce_url="" + enable_announce_pic_popup=false (pearl-sr)
    # serve    = old payload without the two announcement fields
    # notfound = 404 the endpoint entirely (pearl-sr does not route it)
    popup_sdk_config: Literal["disabled", "serve", "notfound"] = "disabled"


class AppConfig(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    player: PlayerSettings = Field(default_factory=PlayerSettings)
    world: WorldSettings = Field(default_factory=WorldSettings)
    gameplay: GameplaySettings = Field(default_factory=GameplaySettings)
    endgame: EndgameSettings = Field(default_factory=EndgameSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    experiments: ExperimentSettings = Field(default_factory=ExperimentSettings)

    @field_validator("paths", mode="after")
    @classmethod
    def _check_paths(cls, value: PathSettings) -> PathSettings:
        return value

    @classmethod
    def load(cls, path: str | Path | None = None) -> AppConfig:
        config_path = Path(path) if path else ROOT / "config.toml"
        if not config_path.exists():
            return cls()
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))

        # Optional world content pack.  config.toml can keep a few overrides
        # next to ``file = "data/world.json"``; the JSON is the base.
        world_raw = raw.get("world")
        if isinstance(world_raw, dict):
            world_file = world_raw.get("file")
            if isinstance(world_file, str) and world_file:
                world_path = Path(world_file)
                if not world_path.is_absolute():
                    world_path = ROOT / world_path
                try:
                    data = json.loads(world_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        merged = dict(data)
                        merged.update({k: v for k, v in world_raw.items() if k != "file"})
                        merged["file"] = str(world_file)
                        raw["world"] = merged
                except (OSError, ValueError) as exc:
                    log.warning("could not read world pack %s: %s", world_path, exc)

        return cls.model_validate(raw)

    # convenience accessors -------------------------------------------------
    @property
    def version_profile_path(self) -> Path:
        return ROOT / "versions" / f"{self.server.profile}.toml"

    @property
    def proto_dir(self) -> Path:
        return self.paths.resolve(self.paths.proto_dir)

    @property
    def data_dir(self) -> Path:
        return self.paths.resolve(self.paths.data_dir)

    @property
    def save_dir(self) -> Path:
        return self.paths.resolve(self.paths.save_dir)

    @property
    def log_dir(self) -> Path:
        return self.paths.resolve(self.paths.log_dir)

    @property
    def hotfix_path(self) -> Path:
        return self.paths.resolve(self.paths.hotfix)
