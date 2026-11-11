from __future__ import annotations

import unittest

from hsr.replay import run_core_flow
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


if __name__ == "__main__":
    unittest.main()
