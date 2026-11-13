"""A protobuf "module" backed by a private descriptor pool.

The generated `StarRail_pb2` module registers its messages in protobuf's default
pool, which forbids two files from defining the same message name.  A server that
ships more than one protocol snapshot (4.6.54 and 4.8/4.7.51 both define
`PlayerLoginScRsp`) therefore cannot import two generated modules at once.

This module builds the same message classes from a `FileDescriptorSet` but inside
its own `DescriptorPool`, so any number of snapshots can be loaded side by side.
Attribute access is lazy: `mod.PlayerLoginCsReq` resolves (and caches) the class on
first use, which keeps startup cheap for a 5000-message schema.
"""

from __future__ import annotations

import types
from pathlib import Path

import importlib
import json
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.internal import enum_type_wrapper

_LEGACY_MODULE = "hsr.protocol.gen.StarRail_pb2"


class EnumProxy:
    """`pb.SomeEnum` for a newer snapshot, with 4.6.54 value names as aliases.

    Field/enum *names* are re-scrambled between client versions while the enum
    *numbers* stay stable, so old code keeps working: `MissionStatus_MissionFinish`
    resolves to 2 even though the 4.8 snapshot calls that value `MISSION_FINISH`.
    """

    def __init__(self, name: str, wrapper, legacy):
        self._name = name
        self._wrapper = wrapper
        self._legacy = legacy

    def __getattr__(self, item: str) -> int:
        values = self._wrapper._enum_type.values_by_name
        if item in values:
            return values[item].number
        if self._legacy is not None:
            legacy_values = self._legacy._enum_type.values_by_name
            if item in legacy_values:
                number = legacy_values[item].number
                if self._wrapper._enum_type.values_by_number.get(number) is not None:
                    return number
        raise AttributeError(f"{self._name} has no value {item}")

    def Value(self, name: str) -> int:
        return getattr(self, name)

    def Name(self, number: int) -> str:
        value = self._wrapper._enum_type.values_by_number.get(number)
        if value is None:
            raise ValueError(f"enum {self._name} has no value {number}")
        return value.name

    def keys(self):
        return list(self._wrapper._enum_type.values_by_name)

    def values(self):
        return [v.number for v in self._wrapper._enum_type.values_by_name.values()]

    def items(self):
        return [(k, v.number) for k, v in self._wrapper._enum_type.values_by_name.items()]


class ProtoModule(types.ModuleType):
    """Module-like object exposing every message/enum of one descriptor set."""

    def __init__(self, name: str, descriptor_set: str | Path):
        super().__init__(name)
        path = Path(descriptor_set)
        fds = descriptor_pb2.FileDescriptorSet()
        fds.ParseFromString(path.read_bytes())
        # 4.8 renames fields (`avatar_type` -> `character_avatar_type`) and moves them
        # to new numbers.  `field_map.json` renames them back to the names handlers
        # already use, which keeps the wire numbers of the new snapshot untouched.
        remap = path.parent / "field_map.json"
        if remap.exists():
            self.renames = json.loads(remap.read_text(encoding="utf-8"))
            self.message_aliases = self.renames.pop("__messages__", {})
            applied = 0
            for file_proto in fds.file:
                applied += _apply_renames(file_proto, self.renames)
            self.renamed_fields = applied
        else:
            self.renames = {}
            self.message_aliases = {}
            self.renamed_fields = 0
        self.pool = descriptor_pool.DescriptorPool()
        for file_proto in fds.file:
            self.pool.Add(file_proto)
        self._cache: dict[str, type] = {}
        self._enums: dict[str, EnumProxy] = {}
        self.descriptor_path = path
        try:
            self._legacy = importlib.import_module(_LEGACY_MODULE)
        except Exception:  # noqa: BLE001 - 4.8-only deployment without the old snapshot
            self._legacy = None

    def cls(self, name: str):
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        target = self.message_aliases.get(name, name)
        try:
            desc = self.pool.FindMessageTypeByName(target)
            klass = message_factory.GetMessageClass(desc)
        except Exception:  # noqa: BLE001 - unknown/renamed message
            return None
        self._cache[name] = klass
        return klass

    def enum(self, name: str):
        try:
            return self.pool.FindEnumTypeByName(name)
        except Exception:  # noqa: BLE001
            return None

    def has(self, name: str) -> bool:
        return self.cls(name) is not None

    def enum_proxy(self, name: str) -> EnumProxy | None:
        proxy = self._enums.get(name)
        if proxy is not None:
            return proxy
        try:
            wrapper = enum_type_wrapper.EnumTypeWrapper(self.pool.FindEnumTypeByName(name))
        except Exception:  # noqa: BLE001 - not an enum of this snapshot
            return None
        legacy = getattr(self._legacy, name, None) if self._legacy is not None else None
        proxy = EnumProxy(name, wrapper, legacy)
        self._enums[name] = proxy
        return proxy

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        klass = self.cls(name)
        if klass is not None:
            setattr(self, name, klass)
            return klass
        proxy = self.enum_proxy(name)
        if proxy is not None:
            setattr(self, name, proxy)
            return proxy
        # Enum value constants (`MissionStatus_MissionFinish`) kept from the old
        # snapshot: numbers are stable across versions even when names are not.
        legacy = self._legacy
        if legacy is not None:
            try:
                value = getattr(legacy, name)
            except AttributeError:
                value = None
            if isinstance(value, int):
                return value
        raise AttributeError(f"{self.__name__}: no message/enum named {name!r}")


def _walk_messages(messages):
    for message in messages:
        yield message
        yield from _walk_messages(message.nested_type)


def _apply_renames(file_proto, table: dict[str, dict[str, str]]) -> int:
    """Rename fields in place: `table[message][4.8 name] = name handlers use`."""
    applied = 0
    for message in _walk_messages(file_proto.message_type):
        renames = table.get(message.name)
        if not renames:
            continue
        used = {field.name for field in message.field}
        for field in message.field:
            target = renames.get(field.name)
            if not target:
                continue
            if target in used:
                raise ValueError(
                    f"field_map: {message.name}.{field.name} -> {target} collides"
                )
            used.add(target)
            field.name = target
            applied += 1
    return applied


def load(name: str, descriptor_set: str | Path) -> ProtoModule:
    return ProtoModule(name, descriptor_set)