"""手动编码 4.7.51（4.8 beta）的 GateServer 线格式。

4.7.51 客户端把 GateServer 的字段号整体重排了（4.6.54: ip=3 / port=9；
4.7.51: ip=15 / port=5），项目的 pb2 是 4.6.54 快照，按名字赋值会写到错误的字段号，
客户端读不到服务器地址 —— 2026-11-13 真机表现是「服务器连接错误」且完全没有 UDP。

字段号来自 ppSR 4.7.5X（同一客户端可正常进入游戏）的 /query_gateway 响应，
用通用 protobuf wire 解析得到；详见 docs/reference/06-4.7.51接入.md。
"""
from __future__ import annotations


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _string(field: int, value: str) -> bytes:
    data = str(value).encode("utf-8")
    return _key(field, 2) + _varint(len(data)) + data


def _uint(field: int, value: int) -> bytes:
    return _key(field, 0) + _varint(int(value))


# ppSR 的 8 个 bool（4.7.51 的字段号）。
UNK_FIELDS = (6, 14, 247, 410, 809, 984, 1140, 1293)


def build_gateway_4751(ip: str, port: int, hf: dict[str, str]) -> bytes:
    """GateServer：ip=15 / port=5 / 资源 URL 与版本号也各在新位置。"""
    parts = [_uint(5, port)]
    parts += [_uint(field, 1) for field in UNK_FIELDS]
    parts += [
        _string(8, hf["ex_resource_url"]),
        _string(9, hf["lua_url"]),
        _string(13, hf["asset_bundle_url"]),
        _string(15, ip),
        _string(1507, hf["asset_bundle_url_b"]),
        _string(1895, hf["ifix_url"]),
        _string(1988, hf["ifix_version"]),
        _string(2011, hf["lua_version"]),
    ]
    return b"".join(parts)
