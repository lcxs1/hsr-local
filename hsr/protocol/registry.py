"""Protocol registry: command ids <-> names <-> generated message classes."""

from __future__ import annotations

import importlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from google.protobuf.message import Message

_ALIAS_SUFFIX = re.compile(r"\d+$")


class ProtocolError(RuntimeError):
    pass


class Registry:
    """Wraps `CmdId.json` + `StarRail_pb2`.

    The command table is not perfectly clean: a few names have numeric suffixes to
    disambiguate duplicates (`GetDataCsReq1`), and one id is shared by a CsReq and a
    ScRsp (`GetMonopolyFriendRankingList*`). Everything here is defensive.
    """

    def __init__(self, proto_dir: str | Path, pb_module: Any | None = None):
        self.proto_dir = Path(proto_dir)
        cmd_path = self.proto_dir / "CmdId.json"
        raw = json.loads(cmd_path.read_text(encoding="utf-8"))
        self.name_to_id: dict[str, int] = {k: int(v) for k, v in raw.items()}
        self.id_to_names: dict[int, list[str]] = defaultdict(list)
        for name, cmd in self.name_to_id.items():
            self.id_to_names[cmd].append(name)

        if pb_module is None:
            pb_module = importlib.import_module("hsr.protocol.gen.StarRail_pb2")
        self.pb = pb_module

    # ------------------------------------------------------------------ ids
    def id_of(self, name: str) -> int:
        try:
            return self.name_to_id[name]
        except KeyError as exc:
            raise ProtocolError(f"unknown command name: {name}") from exc

    def names_of(self, cmd: int) -> list[str]:
        return list(self.id_to_names.get(cmd, ()))

    def name_of(self, cmd: int) -> str:
        names = self.names_of(cmd)
        if not names:
            return f"UNKNOWN_{cmd}"
        # Prefer CsReq for inbound logging; notifications have no request side.
        for suffix in ("CsReq", "Notify", "ScRsp"):
            for name in names:
                if name.endswith(suffix):
                    return name
        return names[0]

    def is_known(self, cmd: int) -> bool:
        return cmd in self.id_to_names

    # ----------------------------------------------------------- message names
    @staticmethod
    def _strip_alias(name: str) -> str:
        return _ALIAS_SUFFIX.sub("", name)

    def rsp_name_of(self, req_name: str) -> str | None:
        if not req_name.endswith("CsReq"):
            return None
        candidate = req_name[:-5] + "ScRsp"
        if candidate in self.name_to_id:
            return candidate
        # A handful of 4.6.5x commands use the older `Rsp` suffix instead of
        # `ScRsp` (e.g. GetMatchPlayDataRsp, cmd 1027).  Without this fallback
        # those requests get no reply at all under `unknown_mode=empty_rsp`.
        legacy = req_name[:-5] + "Rsp"
        if legacy in self.name_to_id:
            return legacy

        base = self._strip_alias(req_name)
        rsp_base = base[:-5] + "ScRsp"
        if rsp_base in self.name_to_id:
            return rsp_base
        legacy_base = base[:-5] + "Rsp"
        if legacy_base in self.name_to_id:
            return legacy_base
        # e.g. GetDataCsReq1 -> GetDataScRsp2 / GetDataScRsp3
        for name in self.name_to_id:
            if name.startswith(rsp_base) and _ALIAS_SUFFIX.fullmatch(name[len(rsp_base):]):
                return name
        return None

    def rsp_for(self, cmd: int) -> str | None:
        for name in self.names_of(cmd):
            if rsp := self.rsp_name_of(name):
                return rsp
        return None

    def message_cls(self, name: str) -> type[Message] | None:
        cls = getattr(self.pb, name, None)
        if cls is not None:
            return cls
        base = self._strip_alias(name)
        if base != name:
            return getattr(self.pb, base, None)
        return None

    # -------------------------------------------------------------- codec
    def decode(self, name: str, body: bytes) -> Message | None:
        cls = self.message_cls(name)
        if cls is None:
            return None
        msg = cls()
        try:
            msg.ParseFromString(body)
        except Exception:  # noqa: BLE001 - malformed client data must not crash us
            return None
        return msg

    def decode_cmd(self, cmd: int, body: bytes) -> tuple[str, Message | None]:
        name = self.name_of(cmd)
        for candidate in self.names_of(cmd):
            msg = self.decode(candidate, body)
            if msg is not None:
                return candidate, msg
        return name, None

    def encode(self, msg: Message, name: str | None = None) -> tuple[int, bytes]:
        msg_name = name or msg.DESCRIPTOR.name
        try:
            cmd = self.id_of(msg_name)
        except ProtocolError:
            # generated class may have the un-suffixed name
            cmd = self.id_of(self._strip_alias(msg_name))
        return cmd, msg.SerializeToString()

    def empty_rsp_bytes(self, cmd: int) -> tuple[int, bytes] | None:
        rsp_name = self.rsp_for(cmd)
        if not rsp_name:
            return None
        cls = self.message_cls(rsp_name)
        if cls is None:
            return None
        return self.id_of(rsp_name), cls().SerializeToString()
