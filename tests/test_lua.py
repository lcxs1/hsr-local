from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hsr.game.lua import LuaConsole


class LuaTest(unittest.TestCase):
    def test_once_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            console = LuaConsole(Path(tmp))
            console.ensure()
            console.set('CS.UnityEngine.Debug.Log("[dsh] hi")', run_once=True)
            first = console.take()
            self.assertIsNotNone(first)
            self.assertIn(b"[dsh] hi", first)
            # marker was rewritten to ft -> no execution now
            self.assertIsNone(console.take())

    def test_always_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            console = LuaConsole(Path(tmp))
            console.set('-- exec=tf\nprint("x")', run_once=False)
            self.assertIsNotNone(console.take())
            self.assertIsNotNone(console.take())


if __name__ == "__main__":
    unittest.main()
