from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hsr.dispatch.local_cdn import LocalCdn

_STUB = "9482fabce7fb0a19183de4d83caf6025"
_REAL = "2de2272805b39ab73f9d901e64b6ffc5"
_BIG = 64 * 1024 + 1


def _manifest(design_hash: str, design_size: int) -> str:
    design = {
        "MajorVersion": 0,
        "MinorVersion": 0,
        "PatchVersion": 17006872,
        "PrevPatch": 0,
        "ContentHash": design_hash,
        "FileSize": design_size,
        "TimeStamp": 1,
        "FileName": "M_DesignV",
        "BaseAssetsDownloadUrl": "",
    }
    native = {
        "MajorVersion": 0,
        "MinorVersion": 0,
        "PatchVersion": 17006872,
        "PrevPatch": 0,
        "ContentHash": "7785ef45507ff37ee3ed0898e1394a36",
        "FileSize": 254,
        "TimeStamp": 1,
        "FileName": "M_NativeDataV",
        "BaseAssetsDownloadUrl": "",
    }
    return json.dumps(design) + "\r\n" + json.dumps(native) + "\r\n"


def _client_dir(root: Path, *, real_bundle: bool = True) -> Path:
    data = root / "StarRail_Data"
    persistent = data / "Persistent"
    streaming = data / "StreamingAssets" / "DesignData" / "Windows"

    (persistent / "Archive" / "Windows").mkdir(parents=True)
    (persistent / "Archive" / "Windows" / "M_ArchiveV.bytes").write_bytes(b"archive")
    (persistent / "Lua" / "Windows").mkdir(parents=True)
    (persistent / "Lua" / "Windows" / "M_LuaV.bytes").write_bytes(b"lua")
    (persistent / "DesignData" / "Windows").mkdir(parents=True)
    (persistent / "DesignData" / "Windows" / f"DesignV_{_STUB}.bytes").write_bytes(b"s" * 1353)
    if real_bundle:
        (persistent / "DesignData" / "Windows" / f"DesignV_{_REAL}.bytes").write_bytes(
            b"x" * _BIG
        )
    streaming.mkdir(parents=True)
    (streaming / "M_Design_ArchiveV.bytes").write_text(
        _manifest(_STUB, 1353), encoding="utf-8"
    )
    return root


class LocalCdnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _client_dir(Path(self.tmp.name))
        self.cdn = LocalCdn(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_indexes_by_basename(self):
        found = self.cdn.lookup("asb/Live/output_1/client/Windows/Archive/M_ArchiveV.bytes")
        self.assertIsInstance(found, Path)
        self.assertEqual(found.name, "M_ArchiveV.bytes")

    def test_design_manifest_points_at_installed_bundle(self):
        manifest = self.cdn.lookup("client/Windows/M_Design_ArchiveV.bytes")
        self.assertIsInstance(manifest, bytes)
        text = manifest.decode("utf-8")
        # 一行一个 JSON（CRLF）；``..`` 会被 4.7.0 的严格解析器当成多余文本
        self.assertTrue(text.endswith("\r\n"))
        self.assertNotIn("..", text)
        self.assertIn(_REAL, text)
        self.assertNotIn(_STUB, text)
        self.assertIn(str(_BIG), text)

    def test_design_manifest_keeps_native_entry(self):
        manifest = self.cdn.lookup("client/Windows/M_Design_ArchiveV.bytes")
        self.assertIn("M_NativeDataV", manifest.decode("utf-8"))

    def test_unknown_file_is_none(self):
        self.assertIsNone(self.cdn.lookup("client/Windows/Deadbeef.bytes"))

    def test_path_traversal_is_ignored(self):
        self.assertIsNone(self.cdn.lookup("../../etc/passwd"))
        self.assertIsNone(self.cdn.lookup(".."))

    def test_without_real_bundle_falls_back_to_streaming_manifest(self):
        with tempfile.TemporaryDirectory() as other:
            root = _client_dir(Path(other), real_bundle=False)
            cdn = LocalCdn(root)
            found = cdn.lookup("client/Windows/M_Design_ArchiveV.bytes")
            self.assertIsInstance(found, Path)
            self.assertEqual(found.name, "M_Design_ArchiveV.bytes")

    def test_unavailable_when_no_star_rail_data(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertFalse(LocalCdn(empty).available())


if __name__ == "__main__":
    unittest.main()
