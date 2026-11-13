"""Process-wide protocol module used by handlers and services.

Handlers do `from hsr.protocol import pb` and then `pb.SomeScRsp(...)`.  Which
protobuf snapshot that resolves to depends on the version profile being served:
the generated 4.6.54 module by default, or the 4.8 snapshot built from
`proto/4.7.51/classes.desc`.

The choice is a context variable, not a global, because two snapshots can be live
in one process (unit tests build 4.6.54 runtimes, the live server serves 4.8).
`Session._run` scopes its worker to `runtime.registry.pb`, so every handler and
service the worker awaits sees the right classes; code that runs outside a
session (tests, offline replay) keeps the legacy default unless it opts in with
`pb.using(...)`.
"""

from __future__ import annotations

import contextlib
import importlib
import types
from collections.abc import Iterator
from contextvars import ContextVar

_LEGACY_MODULE = "hsr.protocol.gen.StarRail_pb2"

_legacy: types.ModuleType | None = None
_current: ContextVar[types.ModuleType | None] = ContextVar("hsr_pb", default=None)


def legacy() -> types.ModuleType:
    """The generated 4.6.54 module (also the default outside a session)."""
    global _legacy
    if _legacy is None:
        _legacy = importlib.import_module(_LEGACY_MODULE)
    return _legacy


def default() -> types.ModuleType:
    return _current.get() or legacy()


def use(module: types.ModuleType) -> None:
    """Bind the current context (and tasks it spawns) to `module`."""
    _current.set(module)


@contextlib.contextmanager
def using(module: types.ModuleType) -> Iterator[None]:
    token = _current.set(module)
    try:
        yield
    finally:
        _current.reset(token)


def active() -> types.ModuleType:
    return default()


def __getattr__(name: str):
    return getattr(default(), name)
