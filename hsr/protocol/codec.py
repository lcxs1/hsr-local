"""Protocol helpers: human-readable dumps and a fallback wire-format decoder."""

from __future__ import annotations

from typing import Any

from google.protobuf import json_format, text_format
from google.protobuf.message import Message


def dump(msg: Message, *, one_line: bool = False) -> str:
    """Pretty-print a generated protobuf message."""
    return text_format.MessageToString(msg, as_one_line=one_line).rstrip()


def to_dict(msg: Message) -> dict[str, Any]:
    return json_format.MessageToDict(msg, preserving_proto_field_name=True)


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def read_fields(data: bytes):
    pos = 0
    while pos < len(data):
        key, pos = read_varint(data, pos)
        field, wire = key >> 3, key & 0x7
        if wire == 0:
            value, pos = read_varint(data, pos)
        elif wire == 2:
            length, pos = read_varint(data, pos)
            value = data[pos : pos + length]
            pos += length
        elif wire == 5:
            value = data[pos : pos + 4]
            pos += 4
        elif wire == 1:
            value = data[pos : pos + 8]
            pos += 8
        else:
            raise ValueError(f"unknown wire type {wire}")
        yield field, wire, value


def dump_wire(data: bytes, indent: int = 0, *, max_depth: int = 6) -> str:
    """Best-effort dump for data we have no descriptor for.

    Length-delimited fields are guessed to be UTF-8 text or a nested message. This is
    intentionally the same wire-type heuristic used by early capture dumpers.
    """
    pad = "  " * indent
    if indent > max_depth:
        return f"{pad}<{data[:32].hex()}...>\n"
    try:
        fields = list(read_fields(data))
    except (ValueError, IndexError):
        return f"{pad}<{data[:64].hex()}>\n"

    out: list[str] = []
    for field, wire, value in fields:
        if wire == 0:
            out.append(f"{pad}{field}: {value}\n")
            continue
        try:
            text = value.decode("utf-8")
            printable = bool(text) and all(c.isprintable() or c in "\r\n\t" for c in text)
        except UnicodeDecodeError:
            printable = False
        if printable:
            out.append(f'{pad}{field}: "{text}"\n')
            continue
        nested = dump_wire(value, indent + 1, max_depth=max_depth)
        if nested.lstrip().startswith("<"):
            out.append(f"{pad}{field}: <{value[:64].hex()}>\n")
        else:
            out.append(f"{pad}{field} {{  ({len(value)} bytes)\n")
            out.append(nested)
            out.append(f"{pad}}}\n")
    return "".join(out)
