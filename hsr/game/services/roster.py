"""Roster: freesr-data.json -> protobuf snapshots.

The client is fed "owned characters" (Avatar) and "path data" (AvatarPathData).
Multi-path characters (Trailblazer 8001..8010, March 1001/1224) are collapsed for
the owned list and kept as separate forms in the path list.
"""

from __future__ import annotations

import logging
from pathlib import Path

from hsr.config import PlayerSettings
from hsr.game.services import freesr
from hsr.protocol import pb

# 所有人共用同一个解锁时间戳（2024-04）。客户端只拿它排序/显示，不校验具体值，
# 换成当前时间反而会让「最近获得」列表变脏。
UNLOCK_TS = 1712924677
FORMAL = pb.AvatarType.AvatarType_AvatarFormalType


TB_BASE = 8001
MARCH_BASE = 1001
TB_PATHS = range(8001, 8011)
MARCH_PATHS = (1001, 1224)


def base_id(avatar_id: int) -> int:
    if avatar_id in MARCH_PATHS:
        return MARCH_BASE
    if avatar_id in TB_PATHS:
        return TB_BASE
    return avatar_id


def path_base(avatar_id: int) -> int:
    """Return the multi-path base for a form id, or 0 for a normal avatar."""
    if avatar_id in TB_PATHS:
        return TB_BASE
    if avatar_id in MARCH_PATHS:
        return MARCH_BASE
    return 0


def relic_uid(relic: freesr.Relic) -> int:
    """Synthetic relic ``unique_id``: freesr uid shifted into the relics range.

    The protocol only requires unique_id to be unique and stable; the 1+ / 2000+
    split keeps relics and lightcones from ever colliding inside the bag/equipment
    lists.
    """
    return 1 + relic.internal_uid


def lightcone_uid(lc: freesr.Lightcone) -> int:
    """Synthetic lightcone ``unique_id``; see :func:`relic_uid` for the scheme."""
    return 2000 + lc.internal_uid


class Roster:
    def __init__(self, data: freesr.FreesrData, player: PlayerSettings):
        self.data = data
        self.player = player
        self.source = "unknown"
        self.load_error: str | None = None
        self.avatars: dict[int, freesr.Avatar] = {int(k): v for k, v in data.avatars.items()}
        self.forms: dict[int, list[freesr.Avatar]] = {}
        for avatar in sorted(self.avatars.values(), key=lambda a: a.avatar_id):
            self.forms.setdefault(base_id(avatar.avatar_id), []).append(avatar)

    # ------------------------------------------------------------------ load
    @classmethod
    def fallback(cls, player: PlayerSettings) -> Roster:
        roster = cls(_fallback_data(), player)
        roster.source = "fallback"
        return roster

    @classmethod
    def load(cls, path: str | Path, player: PlayerSettings, logger: logging.Logger) -> Roster:
        try:
            data = freesr.load(path)
            roster = cls(data, player)
            roster.source = str(path)
            logger.info(
                "freesr loaded: %d avatars, %d relics, %d lightcones",
                len(data.avatars),
                len(data.relics),
                len(data.lightcones),
            )
            return roster
        except FileNotFoundError:
            logger.warning(
                "freesr-data.json not found at %s, using a minimal fallback roster", path
            )
            roster = cls(_fallback_data(), player)
            roster.source = "fallback"
            roster.load_error = f"not found: {path}"
            return roster
        except Exception as exc:  # noqa: BLE001 - bad user file must not prevent startup
            logger.warning("failed to parse %s: %s; using fallback roster", path, exc)
            roster = cls(_fallback_data(), player)
            roster.source = "fallback"
            roster.load_error = f"{path}: {exc}"
            return roster

    # --------------------------------------------------------------- helpers
    def base_id(self, avatar_id: int) -> int:
        return base_id(avatar_id)

    def has_base(self, avatar_id: int) -> bool:
        return base_id(avatar_id) in self.forms

    def has_form(self, avatar_id: int) -> bool:
        return avatar_id in self.avatars

    def repair_lineup(self, slots: list[int], size: int = 4) -> list[int]:
        """Drop unknown/duplicate avatars, then fill from default lineup."""
        out: list[int] = []
        seen: set[int] = set()
        for avatar_id in slots:
            base = base_id(avatar_id)
            if base and base in self.forms and base not in seen:
                out.append(base)
                seen.add(base)
        for base in self.default_lineup():
            if len(out) >= size:
                break
            if base not in seen:
                out.append(base)
                seen.add(base)
        return out[:size]

    def preferred_form(
        self,
        base: int,
        *,
        tb_path: int | None = None,
        march_path: int | None = None,
    ) -> freesr.Avatar | None:
        forms = self.forms.get(base)
        if not forms:
            return None
        wanted = base
        if base == TB_BASE:
            wanted = self.tb_path(tb_path)
        elif base == MARCH_BASE:
            wanted = march_path or self.player.march_multi_path
        for avatar in forms:
            if avatar.avatar_id == wanted:
                return avatar
        for avatar in forms:
            if avatar.avatar_id == base:
                return avatar
        return forms[0]

    def current_form(
        self,
        base: int,
        *,
        tb_path: int | None = None,
        march_path: int | None = None,
    ) -> int:
        form = self.preferred_form(base, tb_path=tb_path, march_path=march_path)
        return form.avatar_id if form is not None else base

    def tb_path(self, override: int | None = None) -> int:
        """Return a Trailblazer form matching ``player.gender``.

        ``gender`` in config.toml is authoritative (1 = male, 2 = female).
        If ``tb_multi_path``/the saved form has the other parity, use the
        equivalent form of the configured gender instead of silently showing a
        model of the wrong gender.
        """
        candidate = override or self.player.tb_multi_path
        if candidate not in TB_PATHS:
            candidate = TB_BASE
        wanted_parity = 0 if self.player.gender == 2 else 1
        if candidate % 2 == wanted_parity:
            return candidate
        other = candidate + 1 if wanted_parity == 0 else candidate - 1
        if other in self.avatars:
            return other
        for path in TB_PATHS:
            if path % 2 == wanted_parity and path in self.avatars:
                return path
        return candidate

    def gender_for(self, tb_path: int | None = None) -> int:
        """Protocol gender comes from config, not from the path parity."""
        _ = tb_path
        return 1 if self.player.gender == 1 else 2

    def _allowed_form(self, avatar_id: int, tb_path: int | None = None) -> bool:
        """Keep only Trailblazer forms of the configured gender.

        ``GetAvatarDataScRsp.avatar_path_data_info_list`` must not mix the male
        and female Traveller: the client would offer both in the path UI.
        ``player.gender`` is authoritative (1 = odd/male, 2 = even/female).
        March 7th and normal avatars are always allowed.
        """
        if avatar_id in TB_PATHS:
            return avatar_id % 2 == (0 if self.gender_for(tb_path) == 2 else 1)
        return True

    def lineup_id(
        self,
        base: int,
        *,
        tb_path: int | None = None,
        march_path: int | None = None,
    ) -> int:
        """Lineup entries use the *form* id for multi-path characters."""
        return self.current_form(base, tb_path=tb_path, march_path=march_path)

    def lightcone_for(self, avatar_id: int) -> freesr.Lightcone | None:
        target = base_id(avatar_id)
        candidates = [lc for lc in self.data.lightcones if base_id(lc.equip_avatar) == target]
        for lc in candidates:
            if lc.equip_avatar == avatar_id:
                return lc
        return candidates[0] if candidates else None

    def relics_for(self, avatar_id: int) -> list[freesr.Relic]:
        target = base_id(avatar_id)
        return [r for r in self.data.relics if base_id(r.equip_avatar) == target]

    def default_lineup(self) -> list[int]:
        """Fallback team used when config/default lineup is unusable.

        Puts the two multi-path bases (March 1001, Trailblazer 8001) first when
        freesr has them, then fills with the remaining ids; if freesr is empty it
        returns the fake 1001..1004 set that :func:`_fallback_data` builds.
        """
        bases = list(self.forms.keys())
        preferred = [b for b in (1001, 8001) if b in bases]
        rest = [b for b in bases if b not in preferred]
        slots = (preferred + rest)[:4]
        return slots or [1001, 1002, 1003, 1004]

    # ------------------------------------------------------------- protobuf
    def build_avatar_sync(
        self,
        equipped: bool = True,
        *,
        tb_path: int | None = None,
        march_path: int | None = None,
        skins: dict[int, int] | None = None,
    ) -> pb.AvatarSync:
        """Build the full avatar snapshot the client caches.

        Two lists, two granularities:
        * ``avatar_list``  —— one entry per *base* id (8001, 1001, ...), what the
          character list shows;
        * ``avatar_path_data_info_list`` —— one entry per *form* id (8001..8010,
          1001/1224), the per-path data; filtered to the configured gender by
          :meth:`_allowed_form`.

        Equipment is attached by base id, so a form switch keeps the lightcone and
        relics.  ``dressed_skin_id`` comes from ``skins`` (base id -> skin id).
        """
        skins = skins or {}
        avatar_list: list[pb.Avatar] = []
        for base in sorted(self.forms):
            form = self.preferred_form(base, tb_path=tb_path, march_path=march_path)
            if form is None:
                continue
            lc = self.lightcone_for(base) if equipped else None
            path = self.current_form(base, tb_path=tb_path, march_path=march_path)
            avatar_list.append(
                pb.Avatar(
                    base_avatar_id=base,
                    level=form.level,
                    promotion=form.promotion,
                    equipment_unique_id=lightcone_uid(lc) if lc else 0,
                    first_met_time_stamp=UNLOCK_TS,
                    cur_multi_path_avatar_type=path,
                    has_taken_promotion_reward_list=[1, 3, 5],
                )
            )

        path_list: list[pb.AvatarPathData] = []
        for avatar in sorted(self.avatars.values(), key=lambda a: a.avatar_id):
            if not self._allowed_form(avatar.avatar_id, tb_path):
                continue
            lc = self.lightcone_for(avatar.avatar_id) if equipped else None
            relics = self.relics_for(avatar.avatar_id) if equipped else []
            data = pb.AvatarPathData(
                avatar_id=avatar.avatar_id,
                rank=avatar.data.rank,
                avatar_path_skill_tree=[
                    pb.AvatarPathSkillTree(point_id=point, level=level)
                    for point, level in avatar.data.skills_by_anchor_type.items()
                    if level > 0
                ],
                equip_relic_list=[
                    pb.EquipRelic(
                        type=relic.relic_id % 10,
                        relic_unique_id=relic_uid(relic),
                    )
                    for relic in relics
                ],
                path_equipment_id=lightcone_uid(lc) if lc else 0,
                unk_enhanced_id=avatar.enhanced_id or 0,
                unlock_timestamp=UNLOCK_TS,
                dressed_skin_id=skins.get(base_id(avatar.avatar_id), 0),
            )
            if avatar.data.ex_rank_id_list:
                # ``path`` above is the *form id* int used for avatar_list; the
                # protobuf object here is ``data``.  Older freesr files had no
                # ex_rank entries, so this typo only surfaced with newer data.
                data.ExRankList.extend(avatar.data.ex_rank_id_list)
            path_list.append(data)

        return pb.AvatarSync(avatar_list=avatar_list, avatar_path_data_info_list=path_list)

    def build_bag(self) -> pb.GetBagScRsp:
        relics = [
            pb.Relic(
                dress_avatar_id=relic.equip_avatar,
                exp=0,
                is_protected=False,
                level=relic.level,
                main_affix_id=relic.main_affix_id,
                tid=relic.relic_id,
                unique_id=relic_uid(relic),
                sub_affix_list=[
                    pb.RelicAffix(affix_id=s.sub_affix_id, cnt=s.count, step=s.step)
                    for s in relic.sub_affixes
                ],
            )
            for relic in self.data.relics
        ]
        equipment = [
            pb.Equipment(
                dress_avatar_id=lc.equip_avatar,
                exp=0,
                is_protected=False,
                level=lc.level,
                promotion=lc.promotion,
                rank=lc.rank,
                tid=lc.item_id,
                unique_id=lightcone_uid(lc),
            )
            for lc in self.data.lightcones
        ]
        return pb.GetBagScRsp(relic_list=relics, equipment_list=equipment)


def _fallback_data() -> freesr.FreesrData:
    avatars = {}
    for avatar_id in (1001, 1002, 1003, 1004):
        avatars[avatar_id] = freesr.Avatar(
            avatar_id=avatar_id,
            data=freesr.InnerData(
                rank=0,
                skills={},
                skills_by_anchor_type={i: 1 for i in range(1, 19)},
            ),
            level=80,
            promotion=6,
            sp_max=100,
            sp_value=0,
            techniques=[],
        )
    return freesr.FreesrData(key="fallback", avatars=avatars)
