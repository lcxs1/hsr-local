"""Export the endgame tables needed for MoC / PF / AS / Peak / Tierce.

Source: the 4.6.54 client tables already extracted by pearl-sr
(``E:\\LCXS\\run\\pearl-sr\\resources``).  Only the rows the endgame modes
reference are kept, so the pack stays small and reviewable.

Output (``data/endgame.json``)::

    {
      "challenges": {"<challenge id>": {...}},   # MoC/PF/AS, ids < 30000 / > 30000
      "peak":       {"stages": {...}, "groups": {...}, "bosses": {...}},
      "tierce":     {"<tierce id>": {...}},
      "stages":     {"<stage id>": {"level": .., "waves": [[..], ..]}},
      "scenes":     {"<plane id>": {"entry_id": .., "groups": {"<group id>": {...}}}},
      "floors":     {"<floor id>": [{"name": .., "value": ..}]},
      "anchors":    {"<entry id>": {"x": .., ...}},
      "counts": {...}
    }

Notes
-----
* The challenge battle stage is the **last** event id of node 1/2
  (``EventIDList1/2[-1]``); that is also what pearl-sr uses.
* A challenge scene is assembled from ``res.json.scene_config``: the maze group
  holds one NPC monster (first entry; the server overrides its monster/event
  id with the challenge config) and all props of that group.
* ``res.json`` is 26 MB, so it is read only here and never copied.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_RESOURCES = Path(r"E:\LCXS\run\pearl-sr\resources")
DEFAULT_OUT = Path("data/endgame.json")

# 大厅 plane 需要单独导出：挑战节点只覆盖 arena（301xx），而 指导「传送」把玩家
# 送进的 忘却之庭 大厅在 plane 10000（列车车厢）。真机日志
# `enter scene: entry=100000104 plane=10000 floor=10000000`；缺这份数据时服务端只能
# 用普通世界装配，客户端只看到角色组 + 凝滞虚影组两个默认组（2026-11-11 真机）。
# 这里按 entry 导出全部 props（挑战水晶就是这些道具），键是 entry id。
LOBBY_PLANES = {
    10000: "忘却之庭大厅（列车车厢），指导「传送」entry 100000104",
}



def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def mode_of(challenge_id: int) -> str:
    if challenge_id < 20000:
        return "MOC"
    if challenge_id < 30000:
        return "PF"
    return "AS"


def _last(values) -> int:
    return int(values[-1]) if values else 0


class Exporter:
    def __init__(self, resources: Path):
        self.resources = resources
        self.entrances = {
            int(e["ID"]): {"plane_id": int(e.get("PlaneID", 0)), "floor_id": int(e.get("FloorID", 0))}
            for e in load_json(resources / "MapEntrance.json")["map_entrance_config"]
        }
        self.planes = {
            int(p["PlaneID"]): p for p in load_json(resources / "MazePlane.json")["maze_plane_config"]
        }
        self.anchors = {
            int(a["entryID"]): self._anchor(a)
            for a in load_json(resources / "Anchor.json")["anchor_config"]
        }
        self.floors = {
            int(f["FloorID"]): [
                {"name": str(v.get("Name", "")), "value": int(v.get("MaxValue", 0))}
                for v in (f.get("SavedValues") or [])
            ]
            for f in load_json(resources / "FloorSavedValuesConfig.json")["saved_values"]
        }
        self.stages = {
            int(s["StageID"]): {
                "level": int(s.get("Level", 80)),
                "waves": [[int(m) for m in wave] for wave in (s.get("MonsterList") or [])],
            }
            for s in load_json(resources / "StageConfig.json")["stage_config"]
        }
        # res.json has several scene entries per plane (different entry ids /
        # variants), so keep them all and merge their entity groups.
        self.scene_config: dict[int, list[dict]] = {}
        for scene in load_json(resources / "res.json")["scene_config"]:
            self.scene_config.setdefault(int(scene["planeID"]), []).append(scene)

        self.challenges: dict[str, dict] = {}
        self.peak_stages: dict[str, dict] = {}
        self.peak_groups: dict[str, dict] = {}
        self.peak_bosses: dict[str, dict] = {}
        self.tierce: dict[str, dict] = {}
        self.used_stages: set[int] = set()
        self.used_planes: dict[int, set[int]] = {}
        self.used_floors: set[int] = set()
        self.missing_stages: set[int] = set()

    @staticmethod
    def _anchor(entry: dict) -> dict:
        items = entry.get("anchor") or []
        if not items:
            return {}
        a = items[0]
        pos = a.get("pos") or {}
        rot = a.get("rot") or {}
        return {
            "x": int(pos.get("x", 0)),
            "y": int(pos.get("y", 0)),
            "z": int(pos.get("z", 0)),
            "rx": int(rot.get("x", 0)),
            "ry": int(rot.get("y", 0)),
            "rz": int(rot.get("z", 0)),
        }

    # ------------------------------------------------------------- resolution
    def resolve(self, entrance_id: int, group_ids: list[int]) -> dict:
        entrance = self.entrances.get(entrance_id, {})
        plane_id = entrance.get("plane_id", 0)
        floor_id = entrance.get("floor_id", 0)
        plane = self.planes.get(plane_id, {})
        world_id = int(plane.get("WorldID", 0))
        for group_id in group_ids:
            if not group_id:
                continue
            self.used_planes.setdefault(plane_id, set()).add(int(group_id))
        if floor_id:
            self.used_floors.add(floor_id)
        return {
            "entrance_id": int(entrance_id),
            "plane_id": plane_id,
            "floor_id": floor_id,
            "world_id": world_id,
        }

    def stage(self, stage_id: int) -> None:
        if not stage_id:
            return
        self.used_stages.add(stage_id)
        if stage_id not in self.stages:
            self.missing_stages.add(stage_id)

    # ---------------------------------------------------------------- tables
    def add_challenges(self) -> None:
        rows = load_json(self.resources / "ChallengeMazeConfig.json")["challenge_config"]
        for row in rows:
            challenge_id = int(row["ID"])
            group1 = int(row.get("MazeGroupID1", 0))
            group2 = int(row.get("MazeGroupID2", 0))
            entrance1 = int(row.get("MapEntranceID", 0))
            entrance2 = int(row.get("MapEntranceID2", 0)) or entrance1
            node1 = self.resolve(entrance1, [group1])
            node2 = self.resolve(entrance2, [group2]) if group2 else node1
            stage1 = _last(row.get("EventIDList1") or [])
            stage2 = _last(row.get("EventIDList2") or [])
            self.stage(stage1)
            self.stage(stage2)
            self.challenges[str(challenge_id)] = {
                "id": challenge_id,
                "mode": mode_of(challenge_id),
                "group_id": int(row.get("GroupID", 0)),
                "floor": int(row.get("Floor", 0) or 0),
                "stage_num": int(row.get("StageNum", 1) or 1),
                "buff_id": int(row.get("MazeBuffID", 0)),
                "target_ids": [int(t) for t in (row.get("ChallengeTargetID") or [])],
                "countdown": int(row.get("ChallengeCountDown", 0) or 0),
                "node1": {
                    **node1,
                    "maze_group_id": group1,
                    "monster_id": _last(row.get("NpcMonsterIDList1") or []),
                    "stage_id": stage1,
                },
                "node2": (
                    {
                        **node2,
                        "maze_group_id": group2,
                        "monster_id": _last(row.get("NpcMonsterIDList2") or []),
                        "stage_id": stage2,
                    }
                    if group2
                    else None
                ),
            }

    def add_peak(self) -> None:
        rows = load_json(self.resources / "ChallengePeakConfig.json")["challenge_peak_config"]
        for row in rows:
            peak_id = int(row["ID"])
            group_id = int(row.get("MazeGroupID", 0))
            node = self.resolve(int(row.get("MapEntranceID", 0)), [group_id])
            stage_id = _last(row.get("EventIDList") or [])
            self.stage(stage_id)
            self.peak_stages[str(peak_id)] = {
                "id": peak_id,
                **node,
                "maze_group_id": group_id,
                "monster_id": _last(row.get("NpcMonsterIDList") or []),
                "stage_id": stage_id,
                "tag_ids": [int(t) for t in (row.get("TagList") or [])],
                "target_ids": [int(t) for t in (row.get("NormalTargetList") or [])],
                "progress": [int(x) for x in (row.get("ProgressValueList") or [])],
                "hp_progress": [int(x) for x in (row.get("HPProgressValueList") or [])],
            }

        for row in load_json(self.resources / "ChallengePeakGroupConfig.json")[
            "challenge_peak_group_config"
        ]:
            self.peak_groups[str(int(row["ID"]))] = {
                "id": int(row["ID"]),
                "boss_level_id": int(row.get("BossLevelID", 0)),
                "pre_level_ids": [int(x) for x in (row.get("PreLevelIDList") or [])],
                "reward_group_id": int(row.get("RewardGroupID", 0) or 0),
            }

        for row in load_json(self.resources / "ChallengePeakBossConfig.json")[
            "challenge_peak_boss_config"
        ]:
            boss_id = int(row["ID"])
            for event_id in row.get("HardEventIDList") or []:
                self.stage(int(event_id))
            self.peak_bosses[str(boss_id)] = {
                "id": boss_id,
                "buff_ids": [int(x) for x in (row.get("BuffList") or [])],
                "hard_event_ids": [int(x) for x in (row.get("HardEventIDList") or [])],
                "hard_tag_ids": [int(x) for x in (row.get("HardTagList") or [])],
                "hard_target": int(row.get("HardTarget", 0) or 0),
                "color_medal_target": int(row.get("ColorMedalTarget", 0) or 0),
            }

    def add_tierce(self) -> None:
        rows = load_json(self.resources / "ChallengeMazeTierceConfig.json")[
            "challenge_tierce_config"
        ]
        for row in rows:
            tierce_id = int(row["ID"])
            group_id = int(row.get("MazeGroupID", 0))
            node = self.resolve(int(row.get("MapEntranceID", 0)), [group_id])
            stage_id = _last(row.get("EventIDList") or [])
            self.stage(stage_id)
            self.tierce[str(tierce_id)] = {
                "id": tierce_id,
                **node,
                "maze_group_id": group_id,
                "monster_id": _last(row.get("NpcMonsterIDList") or []),
                "stage_id": stage_id,
                "target_ids": [int(t) for t in (row.get("ChallengeTargetID") or [])],
                "tierce_target_id": int(row.get("ChallengeTierceTargetID", 0) or 0),
                "pre_challenge_id": int(row.get("PreChallengeMazeID", 0) or 0),
            }

    # ----------------------------------------------------------------- scenes
    def build_scenes(self) -> dict:
        out: dict[str, dict] = {}
        for plane_id, group_ids in sorted(self.used_planes.items()):
            entries = self.scene_config.get(plane_id, [])
            monsters: dict[int, dict] = {}
            props: dict[int, dict] = {}
            entry_id = 0
            for scene in entries:
                entry_id = entry_id or int(scene.get("entryID", 0) or 0)
                for m in scene.get("monsters") or []:
                    group_id = int(m.get("groupId", 0))
                    inst_id = int(m.get("instId", 0))
                    monsters.setdefault(
                        (group_id, inst_id),
                        {
                            "inst_id": inst_id,
                            "monster_id": int(m.get("monsterId", 0)),
                            "event_id": int(m.get("eventId", 0)),
                            "pos": {
                                k: int((m.get("pos") or {}).get(k, 0))
                                for k in ("x", "y", "z")
                            },
                            "rot": {
                                k: int((m.get("rot") or {}).get(k, 0))
                                for k in ("x", "y", "z")
                            },
                        },
                    )
                for p in scene.get("props") or []:
                    group_id = int(p.get("groupId", 0))
                    inst_id = int(p.get("instId", 0))
                    props.setdefault(
                        (group_id, inst_id),
                        {
                            "inst_id": inst_id,
                            "prop_id": int(p.get("propId", 0)),
                            "prop_state": int(p.get("propState", 0)),
                            "pos": {
                                k: int((p.get("pos") or {}).get(k, 0))
                                for k in ("x", "y", "z")
                            },
                            "rot": {
                                k: int((p.get("rot") or {}).get(k, 0))
                                for k in ("x", "y", "z")
                            },
                        },
                    )
            groups: dict[str, dict] = {}
            for group_id in sorted(group_ids):
                group_monsters = [
                    value for (gid, _), value in monsters.items() if gid == group_id
                ]
                group_props = [value for (gid, _), value in props.items() if gid == group_id]
                if not group_monsters and not group_props:
                    continue
                groups[str(group_id)] = {
                    "monsters": group_monsters,
                    "props": group_props,
                }
            out[str(plane_id)] = {"entry_id": entry_id, "groups": groups}
        return out

    # ------------------------------------------------------------------ lobbies
    def build_lobbies(self) -> dict:
        """Per-entry props for the challenge lobby planes (see LOBBY_PLANES)."""
        out: dict[str, dict] = {}
        for plane_id in LOBBY_PLANES:
            entries: dict[str, dict] = {}
            for scene in self.scene_config.get(plane_id, []):
                entry_id = int(scene.get("entryID", 0) or 0)
                if not entry_id:
                    continue
                props = [
                    {
                        "group_id": int(p.get("groupId", 0)),
                        "inst_id": int(p.get("instId", 0)),
                        "prop_id": int(p.get("propId", 0)),
                        "prop_state": int(p.get("propState", 0)),
                        "pos": {k: int((p.get("pos") or {}).get(k, 0)) for k in ("x", "y", "z")},
                        "rot": {k: int((p.get("rot") or {}).get(k, 0)) for k in ("x", "y", "z")},
                    }
                    for p in scene.get("props") or []
                ]
                monsters = [
                    {
                        "group_id": int(m.get("groupId", 0)),
                        "inst_id": int(m.get("instId", 0)),
                        "monster_id": int(m.get("monsterId", 0)),
                        "event_id": int(m.get("eventId", 0)),
                        "pos": {k: int((m.get("pos") or {}).get(k, 0)) for k in ("x", "y", "z")},
                        "rot": {k: int((m.get("rot") or {}).get(k, 0)) for k in ("x", "y", "z")},
                    }
                    for m in scene.get("monsters") or []
                ]
                entries[str(entry_id)] = {"props": props, "monsters": monsters}
            if entries:
                out[str(plane_id)] = {"entries": entries}
        return out

    # ------------------------------------------------------------------- main
    def run(self) -> dict:
        self.add_challenges()
        self.add_peak()
        self.add_tierce()
        stages = {
            str(sid): self.stages[sid]
            for sid in sorted(self.used_stages)
            if sid in self.stages
        }
        scenes = self.build_scenes()
        lobbies = self.build_lobbies()
        floors = {str(fid): self.floors.get(fid, []) for fid in sorted(self.used_floors)}
        entrances = {
            str(eid): {"plane_id": value["plane_id"], "floor_id": value["floor_id"]}
            for eid, value in sorted(self.entrances.items())
        }
        pack = {
            "source": "pearl-sr/resources (Honkai: Star Rail 4.6.54 client tables)",
            "challenges": self.challenges,
            "peak": {
                "stages": self.peak_stages,
                "groups": self.peak_groups,
                "bosses": self.peak_bosses,
            },
            "tierce": self.tierce,
            "stages": stages,
            "scenes": scenes,
            "lobby": lobbies,
            "floors": floors,
            "entrances": entrances,
            "anchors": {str(eid): self.anchors[eid] for eid in sorted(self.anchors)},
            "counts": {
                "challenges": len(self.challenges),
                "moc": sum(1 for c in self.challenges.values() if c["mode"] == "MOC"),
                "pf": sum(1 for c in self.challenges.values() if c["mode"] == "PF"),
                "as": sum(1 for c in self.challenges.values() if c["mode"] == "AS"),
                "peak_stages": len(self.peak_stages),
                "peak_groups": len(self.peak_groups),
                "peak_bosses": len(self.peak_bosses),
                "tierce": len(self.tierce),
                "stages": len(stages),
                "scenes": len(scenes),
                "lobby_planes": len(lobbies),
                "lobby_entries": sum(len(v["entries"]) for v in lobbies.values()),
                "floors": len(floors),
                "missing_stages": len(self.missing_stages),
            },
        }
        return pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", default=str(DEFAULT_RESOURCES))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    resources = Path(args.resources)
    if not (resources / "ChallengeMazeConfig.json").exists():
        raise SystemExit(f"challenge tables not found under {resources}")

    exporter = Exporter(resources)
    pack = exporter.run()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    counts = pack["counts"]
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KiB)")
    print(
        "challenges: {challenges} (MOC {moc} / PF {pf} / AS {as}), "
        "peak {peak_stages} stages / {peak_groups} groups / {peak_bosses} bosses, "
        "tierce {tierce}".format(**counts)
    )
    print(
        "stages: {stages}, scenes: {scenes}, floors: {floors}, "
        "missing stage tables: {missing_stages}".format(**counts)
    )
    if exporter.missing_stages:
        sample = sorted(exporter.missing_stages)[:10]
        print(f"WARNING: {len(exporter.missing_stages)} stage ids missing, e.g. {sample}")


if __name__ == "__main__":
    main()
