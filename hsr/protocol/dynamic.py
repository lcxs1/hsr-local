"""Dynamic protobuf pool for tools.

The server itself uses generated classes (fast). Tools (capture decoder, probes,
future asset explorers) use this pool so they can decode *any* message by name
without importing 4930 generated classes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import Message


class DynamicPool:
    def __init__(self, descriptor_set: str | Path):
        path = Path(descriptor_set)
        fds = descriptor_pb2.FileDescriptorSet()
        fds.ParseFromString(path.read_bytes())
        self.pool = descriptor_pool.DescriptorPool()
        for file_proto in fds.file:
            self.pool.Add(file_proto)
        self._cache: dict[str, type[Message]] = {}

    def cls(self, name: str) -> type[Message] | None:
        if name in self._cache:
            return self._cache[name]
        try:
            desc = self.pool.FindMessageTypeByName(name)
            klass = message_factory.GetMessageClass(desc)
        except Exception:  # noqa: BLE001 - unknown/renamed message
            return None
        self._cache[name] = klass
        return klass

    def decode(self, name: str, body: bytes) -> Message | None:
        klass = self.cls(name)
        if klass is None:
            return None
        msg: Any = klass()
        try:
            msg.ParseFromString(body)
        except Exception:  # noqa: BLE001
            return None
        return msg
