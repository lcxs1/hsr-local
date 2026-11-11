"""Startup validation for the minimal-playable content set.

Nothing here talks to the client.  It cross-checks the files a user is expected
to edit (freesr-data.json, world.json, battle.json, config.toml) so a bad edit
fails loudly instead of degrading into a fallback roster or a wrong stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hsr.runtime import Runtime


class ConfigError(RuntimeError):
    pass


@dataclass
class ConfigReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def lines(self) -> list[str]:
        return [f"ERROR {e}" for e in self.errors] + [f"WARN  {w}" for w in self.warnings]


def _equipment_refs(rt: Runtime) -> tuple[int, int]:
    roster = rt.roster
    unknown = 0
    known = 0
    for lc in roster.data.lightcones:
        if not lc.equip_avatar:
            continue
        if roster.has_base(lc.equip_avatar):
            known += 1
        else:
            unknown += 1
    for relic in roster.data.relics:
        if not relic.equip_avatar:
            continue
        if roster.has_base(relic.equip_avatar):
            known += 1
        else:
            unknown += 1
    return known, unknown


def _duplicates(values: list[int]) -> list[int]:
    seen: set[int] = set()
    dupes: set[int] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def validate(rt: Runtime) -> ConfigReport:
    """Cross-check config + data files; never raises.

    Semantics of the two buckets (this is the part that bites):

    * ``errors`` —— 会让游戏明显不对的问题（角色/关卡不存在、队伍超员、calyx 没有
      映射）。它们只在 ``--strict`` 下致命：``Runtime.load`` 在 strict 且有 error 时
      抛 ``ConfigError``，否则只记日志、继续用兜底数据跑。
    * ``warnings`` —— 能玩但降级（例如 skins 为空、地图可能全黑）。永不中断启动。

    每段对应一个用户会改的文件：freesr → skins → world/scene → 默认+存档队伍 →
    battle → tutorials/progress。
    """
    cfg = rt.cfg
    report = ConfigReport()

    if rt.profile is None:
        report.warnings.append("no version profile loaded; using config.toml fallbacks")
    else:
        if cfg.server.version != rt.profile.version:
            report.warnings.append(
                f"config version {cfg.server.version} != profile {rt.profile.version}"
            )
        if cfg.server.client_build != rt.profile.client_build:
            report.warnings.append(
                f"config build {cfg.server.client_build} != profile {rt.profile.client_build}"
            )

    # 资源校验 / 本地 CDN 回放（guide/01 §3.6、docs/reference/05 §3）----------
    if cfg.features.local_cdn:
        if not cfg.paths.client_dir:
            report.errors.append("features.local_cdn=true 但 [paths].client_dir 为空")
        else:
            client_dir = cfg.paths.resolve(cfg.paths.client_dir)
            if not client_dir.is_dir():
                report.errors.append(f"[paths].client_dir 不存在：{cfg.paths.client_dir}")
            elif not (client_dir / "StarRail_Data").is_dir():
                report.warnings.append(
                    f"[paths].client_dir 下没有 StarRail_Data：{cfg.paths.client_dir}"
                )
    if cfg.features.hotfix_auto:
        report.warnings.append(
            "features.hotfix_auto=true：缺 hotfix 条目时会向官方网关发一次 GET"
            "（AGENTS.md §10 受限例外）"
        )

    # freesr / equipment ----------------------------------------------------
    if rt.roster.load_error:
        report.errors.append(f"freesr-data.json failed to load: {rt.roster.load_error}")
    if not rt.roster.avatars:
        report.errors.append("freesr has no avatars")
    if not rt.roster.data.lightcones and not rt.roster.data.relics:
        report.warnings.append("freesr has no lightcones/relics (equipment will be empty)")
    known, unknown = _equipment_refs(rt)
    if unknown:
        report.warnings.append(
            f"{unknown} equipment entries reference unknown avatars ({known} valid)"
        )

    # skins -----------------------------------------------------------------
    skins = getattr(rt, "skins", None)
    if skins is None or not skins.skins:
        report.warnings.append("data/skins.json is empty; skin switching will have no entries")
    else:
        for avatar_id, skin_id in sorted(rt.save.player.skins.items()):
            if not rt.roster.has_base(avatar_id) or not skins.valid(avatar_id, skin_id):
                report.warnings.append(
                    f"saved skin {skin_id} for avatar {avatar_id} is invalid (will be ignored)"
                )

    # world / scene ---------------------------------------------------------
    world = cfg.world
    if world.floor_id is None or world.entry_id is None:
        report.warnings.append("world floor_id/entry_id missing; using plane_id*1000/100 fallback")
    if not 1 <= world.team_size <= 4:
        report.errors.append(f"world.team_size out of range: {world.team_size}")
    if not world.calyx_entity_id or not world.calyx_prop_id or not world.calyx_group_id:
        report.warnings.append("calyx ids incomplete; calyx may not appear")
    if world.calyx_cocoon_id:
        mapped = rt.battle.cocoon_stage_map.get(world.calyx_cocoon_id)
        if mapped is None:
            report.errors.append(
                f"world calyx cocoon {world.calyx_cocoon_id} is missing from "
                "data/battle.json cocoon_stage_map"
            )
        elif mapped not in rt.battle.stages:
            report.errors.append(
                f"world calyx cocoon {world.calyx_cocoon_id} maps to missing stage {mapped}"
            )
    if not world.teleport_ids:
        report.warnings.append("world.teleport_ids is empty")
    if not world.lighten_section_ranges:
        report.warnings.append("world.lighten_section_ranges is empty; map may render black")
    for start, end in world.lighten_section_ranges:
        if start > end:
            report.warnings.append(f"lighten range is reversed: [{start}, {end}]")

    # default / saved lineup ------------------------------------------------
    default = list(cfg.player.default_lineup)
    if len(default) > world.team_size:
        report.errors.append(
            f"player.default_lineup has {len(default)} entries, team_size={world.team_size}"
        )
    if _duplicates(default):
        report.errors.append(f"player.default_lineup has duplicates: {_duplicates(default)}")
    invalid = [a for a in default if not rt.roster.has_base(a)]
    if invalid:
        report.errors.append(f"player.default_lineup references unknown avatars: {invalid}")

    saved = list(rt.save.player.lineup)
    if saved:
        if _duplicates(saved):
            report.warnings.append(f"saved lineup has duplicates: {_duplicates(saved)}")
        invalid_saved = [a for a in saved if not rt.roster.has_base(a)]
        if invalid_saved:
            report.warnings.append(
                f"saved lineup references unknown avatars (will be repaired): {invalid_saved}"
            )
        if len(saved) > world.team_size:
            report.warnings.append(
                f"saved lineup has {len(saved)} slots, team_size={world.team_size} (will be truncated)"
            )

    # battle ----------------------------------------------------------------
    battle = rt.battle
    if not battle.stages:
        report.warnings.append("stages.json is empty; battle falls back to battle.json")
    if battle.stage_id not in battle.stages and not battle.fallback_waves:
        report.errors.append(f"default battle stage {battle.stage_id} not found in stages.json")
    for cocoon_id, stage_id in sorted(battle.cocoon_stage_map.items()):
        if stage_id not in battle.stages:
            report.warnings.append(
                f"cocoon {cocoon_id} maps to missing stage {stage_id}; default stage will be used"
            )
    for farm_id, stage_id in sorted(battle.farm_element_stage_map.items()):
        if stage_id not in battle.stages:
            report.warnings.append(
                f"farm element {farm_id} maps to missing stage {stage_id}; "
                "farm_element_default_stage will be used"
            )
    if battle.farm_element_default_stage and battle.farm_element_default_stage not in battle.stages:
        report.warnings.append(
            f"farm_element_default_stage {battle.farm_element_default_stage} is not in stages.json"
        )

    # gameplay（秘技点）------------------------------------------------------
    gameplay = cfg.gameplay
    if not 1 <= gameplay.technique_points_max <= 99:
        report.errors.append(
            f"gameplay.technique_points_max out of range: {gameplay.technique_points_max}"
        )
    if gameplay.technique_points_max > 5:
        report.warnings.append(
            f"gameplay.technique_points_max={gameplay.technique_points_max} > 5; "
            "客户端只画 5 个菱形，多出来的点看不到"
        )
    if gameplay.technique_points == "refill" and gameplay.technique_refill_seconds <= 0:
        report.errors.append("gameplay.technique_refill_seconds must be > 0 for refill mode")

    # tutorials / progress --------------------------------------------------
    if not rt.tutorials.tutorials:
        report.warnings.append("tutorials.json is empty (guide popups may return)")
    if not rt.progress.missions and not rt.progress.quests:
        report.warnings.append("progress.json is empty (mission/quest UI will look empty)")

    return report
