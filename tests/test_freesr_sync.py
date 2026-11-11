from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import types
import unittest
from pathlib import Path

from hsr.config import (
    ROOT,
    AppConfig,
    PathSettings,
    PlayerSettings,
    ServerSettings,
    WorldSettings,
)
from hsr.dispatch.app import create_app
from hsr.game.handlers import register_all
from hsr.game.lua import LuaConsole
from hsr.game.router import Router
from hsr.game.services import freesr_sync
from hsr.game.services.battle import BattleData
from hsr.game.services.prefs import PrefsStore
from hsr.game.services.progress import ProgressBook
from hsr.game.services.roster import Roster
from hsr.game.services.save import SaveStore
from hsr.game.services.skins import SkinBook
from hsr.game.services.tutorials import TutorialBook
from hsr.net.session import Session
from hsr.protocol.gen import StarRail_pb2 as pb
from hsr.protocol.registry import Registry
from hsr.runtime import Runtime

log = logging.getLogger("hsr.test")


# --------------------------------------------------------------------- fixtures
def make_doc(
    avatar_ids: list[int],
    relic_uids: list[int] | None = None,
    lightcone_uids: list[int] | None = None,
) -> dict:
    """Minimal but valid freesr document (same shape the SR Tools site posts)."""
    avatars = {}
    for avatar_id in avatar_ids:
        avatars[str(avatar_id)] = {
            "avatar_id": avatar_id,
            "level": 80,
            "promotion": 6,
            "sp_max": 100,
            "sp_value": 50,
            "data": {
                "rank": 0,
                "skills": {},
                "skills_by_anchor_type": {"1": 1},
                "ex_rank_id_list": [],
            },
            "techniques": [],
            "enhanced_id": 0,
        }
    relics = [
        {
            "internal_uid": uid,
            "equip_avatar": 0,
            "level": 15,
            "main_affix_id": 1,
            "relic_id": 61011,
            "relic_set_id": 1,
            "sub_affixes": [{"count": 1, "step": 1, "sub_affix_id": 1}],
        }
        for uid in (relic_uids or [])
    ]
    lightcones = [
        {
            "internal_uid": uid,
            "equip_avatar": 0,
            "item_id": 20000,
            "level": 80,
            "promotion": 6,
            "rank": 1,
        }
        for uid in (lightcone_uids or [])
    ]
    return {"key": "test", "avatars": avatars, "relics": relics, "lightcones": lightcones}


def build_runtime(tmp: Path, doc: dict) -> Runtime:
    freesr_path = tmp / "freesr-data.json"
    freesr_path.write_text(json.dumps(doc, indent=4, ensure_ascii=False), encoding="utf-8")
    cfg = AppConfig(
        server=ServerSettings(game_port=0, http_port=0, kcp_conv_mode="u32_le_token"),
        paths=PathSettings(
            freesr=str(freesr_path),
            save=str(tmp / "player.json"),
            prefs=str(tmp / "server_prefs.json"),
            lua_dir=str(tmp),
        ),
        player=PlayerSettings(uid=4242, name="Tester", gender=1, tb_multi_path=8001),
        world=WorldSettings(team_size=4),
    )
    registry = Registry(ROOT / "proto")
    router = Router(cfg, registry, log)
    register_all(router)
    return Runtime(
        cfg=cfg,
        registry=registry,
        router=router,
        roster=Roster.load(freesr_path, cfg.player, log),
        lua=LuaConsole(tmp),
        logger=log,
        prefs=PrefsStore.load(tmp / "server_prefs.json"),
        save=SaveStore.load(tmp / "player.json"),
        tutorials=TutorialBook(),
        progress=ProgressBook(),
        battle=BattleData(),
        skins=SkinBook(),
    )


class _Transport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendto(self, data: bytes, addr=None) -> None:  # noqa: ANN001
        self.sent.append(data)


def make_session(rt: Runtime) -> tuple[Session, list]:
    """A real Session whose ``send`` records protobuf messages instead of KCP frames."""
    session = Session(rt, _Transport(), ("127.0.0.1", 5555), b"\x00" * 8, 1, 1)
    sent: list = []

    async def record(msg, name=None) -> None:  # noqa: ANN001
        sent.append(msg)

    session.send = record  # type: ignore[method-assign]
    return session, sent


async def call_asgi(
    app: object, method: str, path: str, *, body: bytes = b"", headers: dict | None = None
) -> tuple[int, dict[str, str], bytes]:
    """Drive the FastAPI app over raw ASGI (the venv has no httpx/TestClient)."""
    path_only, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path_only,
        "raw_path": path_only.encode(),
        "root_path": "",
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 21000),
    }
    messages: list[dict] = []
    body_sent = False

    async def receive() -> dict:
        nonlocal body_sent
        if body_sent:
            return {"type": "http.disconnect"}
        body_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)  # type: ignore[operator]
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    raw_headers = next(
        m["headers"] for m in messages if m["type"] == "http.response.start"
    )
    payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    headers_out = {k.decode(): v.decode() for k, v in raw_headers}
    return status, headers_out, payload


def notification(messages: list, cls) -> list:
    return [m for m in messages if isinstance(m, cls)]


class FreesrSyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.doc = make_doc([1001, 8001, 1506], relic_uids=[0, 1], lightcone_uids=[0])
        self.rt = build_runtime(self.dir, self.doc)
        self.session, self.sent = make_session(self.rt)
        self.rt.protocol = types.SimpleNamespace(sessions={1: self.session})

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    async def test_apply_updates_roster_file_and_client(self) -> None:
        new = make_doc([1001, 8001, 1506, 1507], relic_uids=[0], lightcone_uids=[0, 1])
        result = await freesr_sync.apply(
            self.rt, {"data": new, "username": "u", "password": "p"}
        )

        self.assertEqual(result.avatars, 4)
        self.assertEqual(result.relics, 1)
        self.assertEqual(result.lightcones, 2)
        self.assertTrue(result.written)
        self.assertEqual(result.sessions, 1)
        self.assertEqual(result.source, "srtools")

        on_disk = json.loads((self.dir / "freesr-data.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(on_disk["avatars"]), ["1001", "1506", "1507", "8001"])
        self.assertEqual(len(on_disk["relics"]), 1)
        self.assertEqual(len(self.rt.roster.avatars), 4)

        syncs = notification(self.sent, pb.PlayerSyncScNotify)
        self.assertGreaterEqual(len(syncs), 4)
        self.assertEqual(len(syncs[-1].avatar_sync.avatar_list), 4)
        self.assertEqual(
            sorted(a.base_avatar_id for a in syncs[-1].avatar_sync.avatar_list),
            [1001, 1506, 1507, 8001],
        )
        self.assertEqual(len(syncs[-2].equipment_list), 2)
        self.assertEqual(len(syncs[-3].relic_list), 1)

    async def test_stale_items_are_deleted(self) -> None:
        new = make_doc([1001, 8001, 1506], relic_uids=[0], lightcone_uids=[0])
        await freesr_sync.apply(self.rt, {"data": new})

        deletes = [m for m in notification(self.sent, pb.PlayerSyncScNotify) if m.del_relic_list]
        self.assertEqual(len(deletes), 1)
        # internal_uid 1 -> unique_id 2 (relics start at 1)
        self.assertEqual(list(deletes[0].del_relic_list), [2])

    async def test_invalid_payload_keeps_old_roster_and_file(self) -> None:
        path = self.dir / "freesr-data.json"
        before = path.read_text(encoding="utf-8")
        roster = self.rt.roster

        with self.assertRaises(freesr_sync.FreesrPayloadError):
            await freesr_sync.apply(self.rt, {"data": {"avatars": {}, "relics": []}})
        with self.assertRaises(freesr_sync.FreesrPayloadError):
            await freesr_sync.apply(self.rt, {"data": "not json"})
        with self.assertRaises(freesr_sync.FreesrPayloadError):
            await freesr_sync.apply(self.rt, ["a list"])

        self.assertIs(self.rt.roster, roster)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.sent, [])

    async def test_disabled_switch_rejects(self) -> None:
        self.rt.cfg.features.freesr_hot_reload = False
        with self.assertRaises(freesr_sync.FreesrPayloadError):
            await freesr_sync.apply(self.rt, {"data": make_doc([1001])})

    async def test_rejects_wiping_items_without_force(self) -> None:
        path = self.dir / "freesr-data.json"
        before = path.read_text(encoding="utf-8")
        roster = self.rt.roster
        # The page's default state has no relics/lightcones; pushing it back
        # would silently empty the account, so it must be rejected.
        with self.assertRaises(freesr_sync.FreesrPayloadError) as ctx:
            await freesr_sync.apply(self.rt, {"data": make_doc([1001, 8001, 1506])})
        self.assertIn("遗器", str(ctx.exception))
        self.assertIs(self.rt.roster, roster)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.sent, [])

    async def test_force_allows_wiping_items(self) -> None:
        result = await freesr_sync.apply(
            self.rt, {"data": make_doc([1001, 8001, 1506])}, force=True
        )
        self.assertEqual(result.relics, 0)
        on_disk = json.loads((self.dir / "freesr-data.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["relics"], [])

    async def test_reload_from_disk(self) -> None:
        path = self.dir / "freesr-data.json"
        path.write_text(
            json.dumps(make_doc([1001, 1506], relic_uids=[0]), indent=4),
            encoding="utf-8",
        )
        result = await freesr_sync.reload_from_disk(self.rt)
        self.assertFalse(result.written)
        self.assertEqual(result.avatars, 2)
        self.assertEqual(sorted(self.rt.roster.avatars), [1001, 1506])

    async def test_lineup_repaired_when_avatar_disappears(self) -> None:
        self.assertIn(8001, self.session.lineup.slots)
        new = make_doc([1001, 1506], relic_uids=[0, 1], lightcone_uids=[0])
        result = await freesr_sync.apply(self.rt, {"data": new})

        self.assertTrue(result.lineup_changed)
        self.assertNotIn(8001, self.session.lineup.slots)
        self.assertEqual(self.session.lineup.slots, [1001, 1506])
        self.assertTrue(notification(self.sent, pb.SyncLineupNotify))

    async def test_extra_multi_path_switches_form(self) -> None:
        # 8003 = male Trailblazer / Preservation; player.gender=1 accepts it.
        new = make_doc([1001, 8001, 8003, 1506], relic_uids=[0, 1], lightcone_uids=[0])
        result = await freesr_sync.apply(
            self.rt, {"data": new, "multi_path": {"8001": 8003}}
        )
        self.assertEqual(result.path_changes, [8003])
        self.assertEqual(self.session.tb_multi_path, 8003)
        self.assertEqual(self.rt.save.player.tb_multi_path, 8003)

    async def test_file_watch_reloads_changed_file(self) -> None:
        path = self.dir / "freesr-data.json"
        # watch() polls quickly; let one interval elapse and check the roster.
        watcher = asyncio.create_task(freesr_sync.watch(self.rt, interval=0.5))
        try:
            # Let watch() take its baseline signature before touching the file.
            await asyncio.sleep(0)
            path.write_text(json.dumps(make_doc([1001, 1506]), indent=4), encoding="utf-8")
            for _ in range(20):
                await asyncio.sleep(0.1)
                if sorted(self.rt.roster.avatars) == [1001, 1506]:
                    break
            self.assertEqual(sorted(self.rt.roster.avatars), [1001, 1506])
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass


class SrtoolsRouteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rt = build_runtime(self.dir, make_doc([1001, 8001], relic_uids=[0, 1]))
        self.session, self.sent = make_session(self.rt)
        self.rt.protocol = types.SimpleNamespace(sessions={1: self.session})
        self.app = create_app(self.rt)

    async def asyncTearDown(self) -> None:
        await self.session.close("test")
        self.tmp.cleanup()

    async def test_post_srtools_applies_and_answers_cors(self) -> None:
        new = make_doc([1001, 8001, 1506], relic_uids=[0])
        body = json.dumps({"data": new, "username": "u", "password": "p"}).encode()
        status, headers, payload = await call_asgi(
            self.app,
            "POST",
            "/srtools",
            body=body,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        answer = json.loads(payload)
        self.assertEqual(answer["message"], "OK")
        self.assertEqual(answer["status"], 200)
        self.assertEqual(answer["avatars"], 3)
        self.assertTrue(answer["written"])
        self.assertEqual(sorted(self.rt.roster.avatars), [1001, 1506, 8001])
        self.assertTrue(notification(self.sent, pb.PlayerSyncScNotify))

    async def test_probe_without_data_is_ok_and_changes_nothing(self) -> None:
        # 「连接 PS」 first posts {"username","password"} only (see ppSR).
        path = self.dir / "freesr-data.json"
        before = path.read_text(encoding="utf-8")
        status, headers, payload = await call_asgi(
            self.app, "POST", "/srtools", body=b'{"username":"","password":""}'
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        answer = json.loads(payload)
        self.assertTrue(answer["probe"])
        self.assertEqual(answer["avatars"], 2)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.sent, [])

    async def test_options_preflight(self) -> None:
        status, headers, _ = await call_asgi(
            self.app,
            "OPTIONS",
            "/srtools",
            headers={
                "origin": "https://srtools.punklorde.org",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        self.assertEqual(headers.get("access-control-allow-methods"), "GET,POST,OPTIONS")

    async def test_bad_json_is_rejected_with_400(self) -> None:
        status, headers, payload = await call_asgi(
            self.app, "POST", "/srtools", body=b"{oops"
        )
        self.assertEqual(status, 400)
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        self.assertIn("invalid JSON", json.loads(payload)["message"])

    async def test_get_status(self) -> None:
        status, _, payload = await call_asgi(self.app, "GET", "/srtools")
        self.assertEqual(status, 200)
        answer = json.loads(payload)
        self.assertEqual(answer["avatars"], 2)
        self.assertTrue(answer["hot_reload"])
        self.assertEqual(answer["sessions"], 1)
        self.assertEqual(answer["export"], "/srtools/export")

    async def test_export_returns_current_document(self) -> None:
        status, headers, payload = await call_asgi(self.app, "GET", "/srtools/export")
        self.assertEqual(status, 200)
        self.assertIn("freesr-data.json", headers.get("content-disposition", ""))
        self.assertEqual(headers.get("access-control-allow-origin"), "*")
        doc = json.loads(payload)
        self.assertEqual(sorted(doc["avatars"]), ["1001", "8001"])
        self.assertEqual(len(doc["relics"]), 2)

    async def test_post_wipe_is_rejected_unless_forced(self) -> None:
        body = json.dumps({"data": make_doc([1001, 8001])}).encode()
        status, _, payload = await call_asgi(self.app, "POST", "/srtools", body=body)
        self.assertEqual(status, 400)
        self.assertIn("遗器", json.loads(payload)["message"])
        # the same push with ?force=1 goes through
        status, _, payload = await call_asgi(
            self.app, "POST", "/srtools?force=1", body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["relics"], 0)

    async def test_admin_reload(self) -> None:
        path = self.dir / "freesr-data.json"
        path.write_text(json.dumps(make_doc([1001, 1506]), indent=4), encoding="utf-8")
        status, _, payload = await call_asgi(self.app, "POST", "/admin/freesr/reload")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["avatars"], 2)
        self.assertEqual(sorted(self.rt.roster.avatars), [1001, 1506])


if __name__ == "__main__":
    unittest.main()
