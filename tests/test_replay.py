from __future__ import annotations

import unittest

from hsr.replay import run_challenge_flow, run_core_flow
from hsr.runtime import Runtime


class ReplayTest(unittest.IsolatedAsyncioTestCase):
    async def test_core_flow(self):
        rt = Runtime.load()
        before = list(rt.save.player.lineup)
        result = await run_core_flow(rt)
        self.assertEqual(result.battle_ids, [1, 2])
        expected_form = 8003 if rt.cfg.player.gender == 1 else 8004
        self.assertEqual(result.avatar_form, expected_form)
        self.assertEqual(result.lineup_base[0], 8001)
        self.assertIn("battle_retry", result.events)
        self.assertIn("battle_quit", result.events)
        self.assertIn("lineup_hp", result.events)
        self.assertIn("skin_dress", result.events)
        self.assertIn("skin_takeoff", result.events)
        # The replay must not touch the player's real save.
        self.assertEqual(rt.save.player.lineup, before)

    async def test_challenge_flow_official_switch(self):
        """``advance=official``：换半场照抄正式服真机包序，下半照常能打能结算。"""
        rt = Runtime.load()
        before = list(rt.save.player.lineup)
        result = await run_challenge_flow(rt, advance="official")
        self.assertEqual(
            result.events,
            [
                "challenge_start",
                "challenge_battle_1",
                "challenge_advance",
                "challenge_battle_2",
                "challenge_settle",
                "challenge_leave",
            ],
        )
        self.assertEqual(len(result.battle_ids), 2)
        self.assertNotEqual(result.battle_ids[0], result.battle_ids[1])
        self.assertEqual(rt.save.player.lineup, before)

    async def test_challenge_flow(self):
        """混沌回忆上下半换组 → 下半结算 → 退出（下半黑屏那一条链）。"""
        rt = Runtime.load()
        before = list(rt.save.player.lineup)
        result = await run_challenge_flow(rt)
        self.assertEqual(
            result.events,
            [
                "challenge_start",
                "challenge_battle_1",
                "challenge_advance",
                "challenge_battle_2",
                "challenge_settle",
                "challenge_leave",
            ],
        )
        self.assertEqual(len(result.battle_ids), 2)
        self.assertNotEqual(result.battle_ids[0], result.battle_ids[1])
        self.assertEqual(rt.save.player.lineup, before)


if __name__ == "__main__":
    unittest.main()
