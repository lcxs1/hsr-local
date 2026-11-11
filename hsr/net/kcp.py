#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
KCP - A Better ARQ Protocol Implementation
skywind3000 (at) gmail.com, 2010-2011
https://github.com/AFN99

Features:
+ Average RTT reduce 30% - 40% vs traditional ARQ like tcp.
+ Maximum RTT reduce three times vs tcp.
+ Lightweight, distributed as a single source file.

"""

# =============================================================================
# hsr/net/kcp.py —— KCP 可靠传输协议的 Python 实现（第三方代码）
#
# 来源：skywind3000 的 pykcp（MIT），上面的模块 docstring 就是原作者信息。
# 它提供"在 UDP 之上做可靠、有序、低延迟传输"的算法；游戏帧（hsr/net/frame.py）
# 是它的应用层载荷。
#
# 阅读顺序建议：
#   1) 常量 IKCP_*          协议参数
#   2) int64/uint32/int32   模拟 C 的定长整数回绕
#   3) IKCPSEG              一个协议段的结构与编解码
#   4) KCP                  状态机：send/recv/update/flush/input
#
# 这里只加结构性注释，不逐行解释算法；细节以 KCP 原始文档为准。
# =============================================================================
__name__ = 'pykcp'
__author__ = 'skywind3000'
__author_email__ = ''
__license__ = 'MIT License'

import threading
from ctypes import c_uint32, c_int32, c_int64
from collections import deque
from typing import overload

# KCP 协议参数：RTO 上下限、命令字（PUSH/ACK/WASK/WINS）、窗口、MTU 等。
# 本仓库的 MTU 默认就是 1400（config.toml [server].kcp_mtu），与 echium / ppSR 一致。
IKCP_RTO_NDL = 30
IKCP_RTO_MIN = 100
IKCP_RTO_DEF = 200
IKCP_RTO_MAX = 60000
IKCP_CMD_PUSH = 81
IKCP_CMD_ACK = 82
IKCP_CMD_WASK = 83
IKCP_CMD_WINS = 84
IKCP_ASK_SEND = 1
IKCP_ASK_TELL = 2
IKCP_WND_SND = 256
IKCP_WND_RCV = 256
IKCP_MTU_DEF = 1400
IKCP_ACK_FAST = 3
IKCP_INTERVAL = 100
IKCP_OVERHEAD = 28
IKCP_DEADLINK = 20
IKCP_THRESH_INIT = 2
IKCP_THRESH_MIN = 2
IKCP_PROBE_INIT = 7000
IKCP_PROBE_LIMIT = 120000
IKCP_FASTACK_LIMIT = 5
IKCP_FASTACK_CONSERVE = 0

# 用 int 子类模拟 C 的定长整数：每次运算后按 32/64 位截断并处理符号。
# KCP 的 sn/ts 依赖这种回绕语义，直接用 Python 大整数会算错。
class int64(int):
    def __new__(cls, value, *args, **kwargs):
        return super(int64, int64).__new__(cls, c_int64(value).value)
    def __add__(self, other):
        return self.__class__(c_int64(super(int64, self).__add__(other)).value)
    def __sub__(self, other):
        return self.__class__(c_int64(super(int64, self).__sub__(other)).value)
    def __mul__(self, other):
        return self.__class__(c_int64(super(int64, self).__mul__(other)).value)
    def __truediv__(self, other):
        return self.__class__(c_int64(int(super(int64, self).__truediv__(other))).value)
    def __floordiv__(self, other):
        return self.__class__(c_int64(super(int64, self).__floordiv__(other)).value)
    def __mod__(self, other):
        return self.__class__(c_int64(super(int64, self).__mod__(other)).value)
    def __and__(self, other):
        return self.__class__(c_int64(super(int64, self).__and__(other)).value)
    def __or__(self, other):
        return self.__class__(c_int64(super(int64, self).__or__(other)).value)
    def __xor__(self, other):
        return self.__class__(c_int64(super(int64, self).__xor__(other)).value)
    def __invert__(self):
        return self.__class__(c_int64(super(int64, self).__invert__()).value)
    def __lshift__(self, _n):
        return self.__class__(c_int64(super(int64, self).__lshift__(_n)).value)
    def __rshift__(self, _n):
        return self.__class__(c_int64(super(int64, self).__rshift__(_n)).value)
    def __neg__(self):
        return self.__class__(c_int64(super(int64, self).__neg__()).value)
    def __pos__(self):
        return self.__class__(c_int64(super(int64, self).__pos__()).value)
    def __str__(self):
        return '%d' % int(self)
    def __repr__(self):
        return '%d <class int64>' % int(self)

class uint32(int):
    def __new__(cls, value, *args, **kwargs):
        return super(uint32, uint32).__new__(cls, c_uint32(value).value)
    def __add__(self, other):
        return self.__class__(c_uint32(super(uint32, self).__add__(other)).value)
    def __sub__(self, other):
        return self.__class__(c_uint32(super(uint32, self).__sub__(other)).value)
    def __mul__(self, other):
        return self.__class__(c_uint32(super(uint32, self).__mul__(other)).value)
    def __truediv__(self, other):
        return self.__class__(c_uint32(int(super(uint32, self).__truediv__(other))).value)
    def __floordiv__(self, other):
        return self.__class__(c_uint32(super(uint32, self).__floordiv__(other)).value)
    def __mod__(self, other):
        return self.__class__(c_uint32(super(uint32, self).__mod__(other)).value)
    def __and__(self, other):
        return self.__class__(c_uint32(super(uint32, self).__and__(other)).value)
    def __or__(self, other):
        return self.__class__(c_uint32(super(uint32, self).__or__(other)).value)
    def __xor__(self, other):
        return self.__class__(c_uint32(super(uint32, self).__xor__(other)).value)
    def __invert__(self):
        return self.__class__(c_uint32(super(uint32, self).__invert__()).value)
    def __lshift__(self, _n):
        return self.__class__(c_uint32(super(uint32, self).__lshift__(_n)).value)
    def __rshift__(self, _n):
        return self.__class__(c_uint32(super(uint32, self).__rshift__(_n)).value)
    def __neg__(self):
        return self.__class__(c_uint32(super(uint32, self).__neg__()).value)
    def __pos__(self):
        return self.__class__(c_uint32(super(uint32, self).__pos__()).value)
    def __str__(self):
        return '%d' % int(self)
    def __repr__(self):
        return '%d <class uint32>' % int(self)

class int32(int):
    def __new__(cls, value, *args, **kwargs):
        return super(int32, int32).__new__(cls, c_int32(value).value)
    def __add__(self, other):
        return self.__class__(c_int32(super(int32, self).__add__(other)).value)
    def __sub__(self, other):
        return self.__class__(c_int32(super(int32, self).__sub__(other)).value)
    def __mul__(self, other):
        return self.__class__(c_int32(super(int32, self).__mul__(other)).value)
    def __truediv__(self, other):
        return self.__class__(c_int32(int(super(int32, self).__truediv__(other))).value)
    def __floordiv__(self, other):
        return self.__class__(c_int32(super(int32, self).__floordiv__(other)).value)
    def __mod__(self, other):
        return self.__class__(c_int32(super(int32, self).__mod__(other)).value)
    def __and__(self, other):
        return self.__class__(c_int32(super(int32, self).__and__(other)).value)
    def __or__(self, other):
        return self.__class__(c_int32(super(int32, self).__or__(other)).value)
    def __xor__(self, other):
        return self.__class__(c_int32(super(int32, self).__xor__(other)).value)
    def __invert__(self):
        return self.__class__(c_int32(super(int32, self).__invert__()).value)
    def __lshift__(self, _n):
        return self.__class__(c_int32(super(int32, self).__lshift__(_n)).value)
    def __rshift__(self, _n):
        return self.__class__(c_int32(super(int32, self).__rshift__(_n)).value)
    def __neg__(self):
        return self.__class__(c_int32(super(int32, self).__neg__()).value)
    def __pos__(self):
        return self.__class__(c_int32(super(int32, self).__pos__()).value)
    def __str__(self):
        return '%d' % int(self)
    def __repr__(self):
        return '%d <class int32>' % int(self)

# 计算 later - earlier，并转成 32 位有符号差值（处理时间戳回绕）。
def _itimediff(later, earlier):
    return int32(uint32(later) - earlier)

# 一个 KCP 段（固定长度头 + 数据）。字段对应协议头：conv/cmd/frg/wnd/ts/sn/una。
# encode/decode 负责头的序列化；同一个方法名有多个重载版本。
class IKCPSEG:
    """
    KCP packet object, which describes the relevant information of the packet
    """
    conv = int64(0)
    cmd = uint32(0)
    frg = uint32(0)
    wnd = uint32(0)
    ts = uint32(0)
    sn = uint32(0)
    una = uint32(0)
    resendts = uint32(0)
    rto = uint32(0)
    fastack = uint32(0)
    xmit = uint32(0)
    data = bytes()

    def __init__(self, conv=0, cmd=0, frg=0, wnd=0, ts=0, sn=0, una=0, data=bytes()):
        self.conv = int64(conv)
        self.cmd = uint32(cmd)
        self.frg = uint32(frg)
        self.wnd = uint32(wnd)
        self.ts = uint32(ts)
        self.sn = uint32(sn)
        self.una = uint32(una)
        self.data = bytes(data)

    def __len__(self):
        return len(self.data)

    def __str__(self):
        return 'conv:%d cmd:%02x frg:%d wnd:%d ts:%d sn:%d una:%d resendts:%d rto:%d fastack:%d xmit:%d data:%d' % (
            self.conv, self.cmd, self.frg, self.wnd, self.ts, self.sn, self.una, self.resendts, self.rto, self.fastack, self.xmit, len(self.data))

    def __encode_data(self, buf, pos):
        end = pos + len(self.data)
        buf[pos:end] = self.data
        return end

    def total_size(self):
        return IKCP_OVERHEAD + len(self.data)

    def __encode_head(self, buf, pos):
        # 头部各字段按大端写入 buf，返回写入后的新位置
        def _encode64s_be(ptr, pos, v):
            ptr[pos + 0] = (v >> 56) & 0xFF
            ptr[pos + 1] = (v >> 48) & 0xFF
            ptr[pos + 2] = (v >> 40) & 0xFF
            ptr[pos + 3] = (v >> 32) & 0xFF
            ptr[pos + 4] = (v >> 24) & 0xFF
            ptr[pos + 5] = (v >> 16) & 0xFF
            ptr[pos + 6] = (v >> 8) & 0xFF
            ptr[pos + 7] = (v >> 0) & 0xFF
            return 8

        def _encode8s_be(ptr: bytearray, pos: int, value: int) -> int:
            ptr[pos] = value & 0xFF
            return 1

        def _endcode16s_be(ptr: bytearray, pos: int, value: int) -> int:
            ptr[pos] = value & 0xFF
            ptr[pos + 1] = (value >> 8) & 0xFF
            return 2

        def _encode32s_be(ptr: bytearray, pos: int, value: int) -> int:
            ptr[pos + 0] = (value >> 0) & 0xFF
            ptr[pos + 1] = (value >> 8) & 0xFF
            ptr[pos + 2] = (value >> 16) & 0xFF
            ptr[pos + 3] = (value >> 24) & 0xFF
            return 4

        pos += _encode64s_be(buf, pos, self.conv)
        pos += _encode8s_be(buf, pos, self.cmd)
        pos += _encode8s_be(buf, pos, self.frg)
        pos += _endcode16s_be(buf, pos, self.wnd)
        pos += _encode32s_be(buf, pos, self.ts)
        pos += _encode32s_be(buf, pos, self.sn)
        pos += _encode32s_be(buf, pos, self.una)
        pos += _encode32s_be(buf, pos, len(self.data))

        return pos

    @overload
    def encode_head(self, buf: bytearray, pos: int) -> int:
        pass

    @overload
    def encode_head(self) -> bytes:
        pass

    def encode_head(self, *args):
        if len(args) == 0:
            buffer = bytearray(IKCP_OVERHEAD)
            self.__encode_head(buffer, 0)
            return bytes(buffer)
        elif len(args) == 2:
            return self.__encode_head(args[0], args[1])

        raise TypeError('takes 2 positional arguments but %d were given' % len(args))

    @overload
    def encode(self, buf: bytearray, pos: int) -> int:
        pass

    @overload
    def encode(self) -> bytes:
        pass

    def encode(self, *args):
        if len(args) == 0:
            buffer = bytearray(IKCP_OVERHEAD + len(self.data))
            pos = self.__encode_head(buffer, 0)
            self.__encode_data(buffer, pos)
            return bytes(buffer)
        elif len(args) == 2:
            pos = self.__encode_head(args[0], args[1])
            return self.__encode_data(args[0], pos)

        raise TypeError('takes 2 positional arguments but %d were given' % len(args))

    @staticmethod
    def decode(buf, pos=0):
        def read_8s(ptr, pos):
            val = ptr[pos]
            return val - 256 if val > 127 else val, 1

        def read_8u(ptr, pos):
            return (ptr[pos] & 0xFF, 1)

        def read_64s_be(ptr: bytearray, pos: int) -> tuple[int, int]:
            raw = 0
            for i in range(8):
                raw = (raw << 8) | (ptr[pos + i] & 0xFF)
            if raw & 0x8000000000000000:
                raw -= 0x10000000000000000
            return (raw, 8)

        def read_32s_le(ptr: bytearray, pos: int) -> tuple[int, int]:
            val = 0
            for i in range(4):
                val |= (ptr[pos + i] & 0xFF) << (8 * i)
            if val & 0x80000000:
                val -= 0x100000000
            return (val, 4)

        def read_16u_le(ptr: bytearray, pos: int) -> tuple[int, int]:
            val = (ptr[pos] & 0xFF) | ((ptr[pos + 1] & 0xFF) << 8)
            return (val, 2)

        r = read_64s_be(buf, pos); conv = r[0]; pos += r[1]
        r = read_8s(buf, pos); cmd = r[0]; pos += r[1]
        r = read_8u(buf, pos); frg = r[0]; pos += r[1]
        r = read_16u_le(buf, pos); wnd = r[0]; pos += r[1]
        r = read_32s_le(buf, pos); ts = r[0]; pos += r[1]
        r = read_32s_le(buf, pos); sn = r[0]; pos += r[1]
        r = read_32s_le(buf, pos); una = r[0]; pos += r[1]
        r = read_32s_le(buf, pos); data_len = r[0]; pos += r[1]

        if cmd != IKCP_CMD_PUSH and cmd != IKCP_CMD_ACK \
                and cmd != IKCP_CMD_WASK and cmd != IKCP_CMD_WINS:
            raise TypeError('Decoding unknown package type')

        if len(buf) < pos + data_len:
            raise IndexError('index out of range')

        seg = IKCPSEG(conv=conv, cmd=cmd, frg=frg, wnd=wnd, ts=ts, sn=sn, una=una, data=buf[pos:pos + data_len])
        return seg










# KCP 会话主体。一个实例对应一个 conv（客户端）。
# 关键点：它自己不碰 socket —— 所有底层发送都通过 self.output 回调，
# 本仓库由 hsr/net/session.py 的 Session.__init__ 把这个回调设成 Session._output。
class KCP:
    """
    KCP packet object
    """
    def __init__(self, conv):
        """
        // create a new kcp control object, 'conv' must equal in two endpoint
        """
        self.conv = int64(conv)
        self.snd_una = uint32(0)
        self.snd_nxt = uint32(0)
        self.rcv_nxt = uint32(0)
        self.ts_recent = uint32(0)
        self.ts_lastack = uint32(0)
        self.ts_probe = uint32(0)
        self.probe_wait = uint32(0)
        self.snd_wnd = uint32(IKCP_WND_SND)
        self.rcv_wnd = uint32(IKCP_WND_RCV)
        self.rmt_wnd = uint32(IKCP_WND_RCV)
        self.cwnd = uint32(0)
        self.incr = uint32(0)
        self.probe = uint32(0)
        self.mtu = uint32(IKCP_MTU_DEF)
        self.mss = uint32(self.mtu - IKCP_OVERHEAD)
        self.stream = False

        self.buffer = bytearray((self.mtu + IKCP_OVERHEAD) * 3)
        self.buflen = 0

        self.snd_queue = deque()
        self.rcv_queue = deque()
        self.snd_buf = deque()
        self.rcv_buf = deque()

        self.acklist = deque()
        self.state = uint32(0)
        self.rx_srtt = int32(0)
        self.rx_rttval = int32(0)
        self.rx_rto = int32(IKCP_RTO_DEF)
        self.rx_minrto = int32(IKCP_RTO_MIN)
        self.current = uint32(0)
        self.interval = uint32(IKCP_INTERVAL)
        self.ts_flush = uint32(IKCP_INTERVAL)
        self.nodly = 0
        self.updated = False
        self.ssthresh = uint32(IKCP_THRESH_INIT)
        self.fastresend = 0
        self.fastlimit = IKCP_FASTACK_LIMIT
        self.nocwnd = 0
        self.xmit = uint32(0)
        self.dead_link = uint32(IKCP_DEADLINK)
        self.mutex = threading.Lock()

    def __str__(self):
        return 'conv:%d'

    # 底层发包出口。KCP 只负责"该发什么"，真正 sendto 由外部注入的这个函数完成。
    def output(self, buf):
        pass

    # 从接收队列取出一个完整应用层消息；ispeek=True 时只看大小、不出队。
    # 本仓库在 Session.feed 里循环调用它，直到返回空 bytes 为止。
    def recv(self, ispeek=False):
        with self.mutex:
            peeksize = 0
            recover = False
            buf = bytes()

            count = len(self.rcv_queue)
            if count <= 0:
                return buf

            peeksize = self.peeksize()
            if peeksize < 0:
                return buf

            if len(self.rcv_queue) >= self.rcv_wnd:
                recover = True

            if ispeek:
                for item in self.rcv_queue:
                    buf += item.data
                    if item.frg == 0:
                        break
            else:
                while count > 0:
                    item = self.rcv_queue.popleft()
                    buf += item.data
                    count -= 1
                    if item.frg == 0:
                        break

            while len(self.rcv_buf) > 0:
                seg = self.rcv_buf[0]
                if seg.sn == self.rcv_nxt and len(self.rcv_queue) < self.rcv_wnd:
                    self.rcv_buf.popleft()
                    self.rcv_queue.append(seg)
                    self.rcv_nxt += 1
                else:
                    break

            if len(self.rcv_queue) < self.rcv_wnd and recover:
                self.probe |= uint32(IKCP_ASK_TELL)

            return buf

    # 把一条应用层消息切成 ≤ mss 的分片，按发送窗口塞进 snd_queue。
    # 这里不真正发包，等 update()/flush() 推进。
    def send(self, buf):
        with self.mutex:
            buf = bytes(buf)
            if isinstance(buf, bytes) == False:
                raise TypeError('argument must be bytes-like object, not ' + type(buf).__name__)
            buf_len = len(buf)
            if self.stream:
                if len(self.snd_queue) > 0:
                    last_seg = self.snd_queue[-1]

                    if len(last_seg) < self.mss:
                        capacity = self.mss - len(last_seg)
                        extend = buf_len if buf_len < capacity else capacity

                        last_seg.data += buf[:extend]
                        buf = buf[extend:]
                        buf_len -= extend

                if buf_len <= 0:
                    return

            if buf_len <= self.mss:
                count = 1
            else:
                count = int((buf_len + self.mss - 1) / self.mss)

            if count >= IKCP_WND_RCV:
                max_len = str(IKCP_WND_RCV * self.mss)
                raise BufferError('The length of buffer exceeds the maximum value. ' + max_len)

            if count == 0:
                count = 1

            while count > 0:
                count -= 1
                size = self.mss if buf_len > self.mss else buf_len
                # 条件写成 not self.stream 是为了和原程序的分支顺序一致
                frg = count if not self.stream else 0
                seg = IKCPSEG(conv=self.conv, cmd=IKCP_CMD_PUSH, frg=frg, data=buf[:size])
                self.snd_queue.append(seg)
                buf = buf[size:]
                buf_len -= size

    def peeksize(self):
        if len(self.rcv_queue) <= 0:
            return -1

        seg = self.rcv_queue[0]
        if seg.frg == 0:
            return len(seg)

        if len(self.rcv_queue) < seg.frg + 1:
            return -1

        length = 0
        for piece in self.rcv_queue:
            length += len(piece)
            if piece.frg == 0:
                break
        return length

    # 推进状态机：把到期的段标记重传、处理窗口探测、按需 flush。
    # current 是毫秒时间戳，由调用方传入（本仓库用 hsr/net/session.py 的 now_ms()：
    # time.monotonic() * 1000 再截成 32 位，因此跨重启不回绕）。
    def update(self, current):
        self.current = uint32(current)
        if self.updated == False:
            self.updated = True
            self.ts_flush = self.current
        slap = _itimediff(self.current, self.ts_flush)
        if slap >= 10000 or slap < -10000:
            self.ts_flush = self.current
            slap = 0
        if slap >= 0:
            self.ts_flush += self.interval
            if _itimediff(self.current, self.ts_flush) >= 0:
                self.ts_flush = self.current + self.interval
            self.flush()

    # 把发送缓冲里"现在能发"的段编码后交给 output()；同时把纯 ACK 也发出去。
    # 这是 KCP 里最核心的发包函数。
    def flush(self):
        with self.mutex:
            if self.updated == False:
                return

            def wnd_unused():
                r_len = len(self.rcv_queue)
                r_win = self.rcv_wnd
                if r_len < r_win:
                    return r_win - r_len
                return 0

            current = self.current

            curr_wnd = wnd_unused()

            self.buflen = 0

            while len(self.acklist) > 0:
                ack = self.acklist.popleft()
                if self.buflen + IKCP_OVERHEAD > self.mtu:
                    self.output(bytes(self.buffer[:self.buflen]))
                    self.buflen = 0
                ack.wnd = curr_wnd
                ack.una = self.rcv_nxt
                self.buflen = ack.encode(self.buffer, self.buflen)

            if self.rmt_wnd == 0:
                if self.probe_wait == 0:
                    self.probe_wait = uint32(IKCP_PROBE_INIT)
                    self.ts_probe = self.current + self.probe_wait
                else:
                    if _itimediff(self.current, self.ts_probe) >= 0:
                        if self.probe_wait < IKCP_PROBE_INIT:
                            self.probe_wait = uint32(IKCP_PROBE_INIT)
                        self.probe_wait += self.probe_wait / 2
                        if self.probe_wait > IKCP_PROBE_LIMIT:
                            self.probe_wait = uint32(IKCP_PROBE_LIMIT)
                        self.ts_probe = self.current + self.probe_wait
                        self.probe |= uint32(IKCP_ASK_SEND)
            else:
                self.ts_probe = uint32(0)
                self.probe_wait = uint32(0)

            if self.probe & IKCP_ASK_SEND:
                wask = IKCPSEG(conv=self.conv, cmd=IKCP_CMD_WASK, wnd=curr_wnd, una=self.rcv_nxt)
                if self.buflen + IKCP_OVERHEAD > self.mtu:
                    self.output(bytes(self.buffer[:self.buflen]))
                    self.buflen = 0
                self.buflen = wask.encode(self.buffer, self.buflen)

            if self.probe & IKCP_ASK_TELL:
                tell = IKCPSEG(conv=self.conv, cmd=IKCP_CMD_WINS, wnd=curr_wnd, una=self.rcv_nxt)
                if self.buflen + IKCP_OVERHEAD > self.mtu:
                    self.output(bytes(self.buffer[:self.buflen]))
                    self.buflen = 0
                self.buflen = tell.encode(self.buffer, self.buflen)

            self.probe = uint32(0)

            cwnd = min(self.snd_wnd, self.rmt_wnd)
            if not self.nocwnd:
                cwnd = min(self.cwnd, cwnd)

            while _itimediff(self.snd_nxt, self.snd_una + cwnd) < 0:
                if len(self.snd_queue) <= 0:
                    break
                seg = self.snd_queue.popleft()
                seg.wnd = curr_wnd
                seg.ts = current
                seg.sn = self.snd_nxt
                seg.una = self.rcv_nxt
                seg.resendts = current
                seg.rto = self.rx_rto
                self.snd_buf.append(seg)
                self.snd_nxt += 1

            if self.fastresend > 0:
                resent = uint32(self.fastresend)
            else:
                resent = uint32(0xFFFFFFFF)

            if self.nodly == 0:
                rtomin = uint32(self.rx_rto >> 3)
            else:
                rtomin = uint32(0)

            lost = False
            change = 0

            for segment in self.snd_buf:
                needsend = False
                if segment.xmit == 0:
                    needsend = True
                    segment.xmit += 1
                    segment.rto = self.rx_rto
                    segment.resendts = current + segment.rto + rtomin
                elif _itimediff(current, segment.resendts) >= 0:
                    needsend = True
                    segment.xmit += 1
                    self.xmit += 1
                    if self.nodly == 0:
                        segment.rto += max(segment.rto, self.rx_rto)
                    else:
                        step = segment.rto if self.nodly < 2 else self.rx_rto
                        segment.rto += step / 2
                    segment.resendts = current + segment.rto
                    lost = True
                elif segment.fastack >= resent:
                    if segment.xmit <= self.fastlimit or self.fastlimit <= 0:
                        needsend = True
                        segment.xmit += 1
                        segment.fastack = uint32(0)
                        segment.resendts = current + segment.rto
                        change += 1
                if needsend:
                    segment.ts = current
                    segment.wnd = curr_wnd
                    segment.una = self.rcv_nxt
                    need = segment.total_size()
                    if self.buflen + need > self.mtu:
                        self.output(bytes(self.buffer[:self.buflen]))
                        self.buflen = 0
                    self.buflen = segment.encode(self.buffer, self.buflen)
                    if segment.xmit >= self.dead_link:
                        self.state = -1

            if self.buflen > 0:
                self.output(bytes(self.buffer[:self.buflen]))
                self.buflen = 0

            if change > 0:
                inflight = self.snd_nxt - self.snd_una
                self.ssthresh = inflight / 2
                if self.ssthresh < IKCP_THRESH_MIN:
                    self.ssthresh = uint32(IKCP_THRESH_MIN)
                self.cwnd = self.ssthresh + resent
                self.incr = self.cwnd * self.mss

            if lost:
                self.ssthresh = uint32(cwnd / 2)
                if self.ssthresh < IKCP_THRESH_MIN:
                    self.ssthresh = IKCP_THRESH_MIN
                self.cwnd = uint32(1)
                self.incr = self.mss

            if self.cwnd < 1:
                self.cwnd = uint32(1)
                self.incr = self.mss

    def parse_data(self, newseg):
        sn = newseg.sn
        repeat = 0
        index = 0

        if _itimediff(sn, self.rcv_nxt + self.rcv_wnd) >= 0 or _itimediff(sn, self.rcv_nxt) < 0:
            return

        for item in self.rcv_buf:
            if item.sn == sn:
                repeat = 1
                break
            if item.sn > sn:
                break
            index += 1

        if repeat == 0:
            self.rcv_buf.insert(index, newseg)

        while len(self.rcv_buf) > 0:
            seg = self.rcv_buf[0]
            if seg.sn == self.rcv_nxt and len(self.rcv_queue) < self.rcv_wnd:
                self.rcv_buf.popleft()
                self.rcv_queue.append(seg)
                self.rcv_nxt += 1
            else:
                break

    # 解析收到的底层包：按命令字分发到 ACK / PUSH / WASK / WINS 处理逻辑，
    # PUSH 的数据会按 sn 排序、去重后放进 rcv_queue（乱序时先缓存）。
    def input(self, data):
        with self.mutex:
            if isinstance(data, bytes) == False:
                raise TypeError('argument must be bytes-like object, not ' + type(data).__name__)

            def parse_una(_una):
                while len(self.snd_buf) > 0:
                    if _itimediff(_una, self.snd_buf[0].sn) > 0:
                        self.snd_buf.popleft()
                    else:
                        break

            def shrink_buf():
                if len(self.snd_buf) > 0:
                    self.snd_una = self.snd_buf[0].sn
                else:
                    self.snd_una = self.snd_nxt

            def update_ack(rtt):
                if self.rx_srtt == 0:
                    self.rx_srtt = rtt
                    self.rx_rttval = rtt / 2
                else:
                    delta = rtt - self.rx_srtt
                    if delta < 0:
                        delta = -delta
                    self.rx_rttval = (int32(3 * self.rx_rttval) + delta) / 4
                    self.rx_srtt = (int32(7 * self.rx_srtt) + rtt) / 8
                    if self.rx_srtt < 1:
                        self.rx_srtt = 1
                rto = self.rx_srtt + max(self.interval, int32(4 * self.rx_rttval))
                self.rx_rto = int32(min(max(self.rx_minrto, rto), int32(IKCP_RTO_MAX)))

            def parse_ack(sn):
                if _itimediff(sn, self.snd_una) < 0 or _itimediff(sn, self.snd_nxt) >= 0:
                    return
                for item in self.snd_buf:
                    if sn == item.sn:
                        self.snd_buf.remove(item)
                        break
                    if _itimediff(sn, item.sn) < 0:
                        break

            def parse_fastack(sn, ts):
                if _itimediff(sn, self.snd_una) < 0 or _itimediff(sn, self.snd_nxt) >= 0:
                    return
                for s in self.snd_buf:
                    if _itimediff(sn, s.sn) < 0:
                        break
                    # 原程序此处是取反后的 continue（保持字节码一致）
                    elif not (sn != s.sn):
                        continue
                    if IKCP_FASTACK_CONSERVE:
                        s.fastack += 1
                    elif _itimediff(ts, s.ts) >= 0:
                        s.fastack += 1

            prev_una = self.snd_una

            size = len(data)
            buf = data
            pos = 0
            flag = False
            maxack = 0
            latest_ts = 0

            while size - pos >= IKCP_OVERHEAD:
                try:
                    seg = IKCPSEG.decode(buf, pos)
                except:
                    return

                pos += seg.total_size()

                if seg.conv != self.conv:
                    return

                self.rmt_wnd = seg.wnd

                parse_una(seg.una)
                shrink_buf()

                if seg.cmd == IKCP_CMD_ACK:
                    t = _itimediff(self.current, seg.ts)
                    if t >= 0:
                        update_ack(int32(t))
                    parse_ack(seg.sn)
                    shrink_buf()
                    if flag == False:
                        flag = True
                        maxack = seg.sn
                        latest_ts = seg.ts
                    else:
                        if _itimediff(seg.sn, maxack) > 0:
                            if IKCP_FASTACK_CONSERVE:
                                maxack = seg.sn
                                latest_ts = seg.ts
                            else:
                                if _itimediff(seg.ts, latest_ts) > 0:
                                    maxack = seg.sn
                                    latest_ts = seg.ts
                elif seg.cmd == IKCP_CMD_PUSH:
                    if _itimediff(seg.sn, self.rcv_nxt + self.rcv_wnd) < 0:
                        ack_seg = IKCPSEG(conv=seg.conv, cmd=IKCP_CMD_ACK, frg=seg.frg, ts=seg.ts, sn=seg.sn)
                        self.acklist.append(ack_seg)
                    if _itimediff(seg.sn, self.rcv_nxt) >= 0:
                        self.parse_data(seg)
                elif seg.cmd == IKCP_CMD_WASK:
                    self.probe |= uint32(IKCP_ASK_TELL)
                elif seg.cmd == IKCP_CMD_WINS:
                    pass
                else:
                    return

            if flag:
                parse_fastack(maxack, latest_ts)

            if _itimediff(self.snd_una, prev_una) > 0:
                if self.cwnd < self.rmt_wnd:
                    mss = self.mss
                    if self.cwnd < self.ssthresh:
                        self.cwnd += 1
                        self.incr += mss
                    else:
                        if self.incr < mss:
                            self.incr = mss
                        self.incr += (mss * mss) / self.incr + (mss / 16)
                        if (self.cwnd + 1) * mss <= self.incr:
                            # 原程序此处有一条 pass，编译出 NOP
                            pass
                            self.cwnd = (self.incr + mss - 1) / (mss if mss > 0 else uint32(1))
                    if self.cwnd > self.rmt_wnd:
                        self.cwnd = self.rmt_wnd
                        self.incr = self.rmt_wnd * mss

    # 根据 current 算出距离下次 update 还有多久（KCP 的自适应时钟）。
    def check(self, current):
        """
        Determine when should you invoke update:
        returns when you should invoke update in millisec, if there
        is no ikcp input/send calling. you can call update in that
        time, instead of call update repeatly.
        Important to reduce unnacessary update invoking. use it to
        schedule update (eg. implementing an epoll-like mechanism,
        or optimize update when handling massive kcp connections)
        """
        if self.updated == False:
            return current
        ts_flush = self.ts_flush

        if _itimediff(current, self.ts_flush) >= 10000 or _itimediff(current, self.ts_flush) < -1000:
            ts_flush = current
        if _itimediff(current, ts_flush) >= 0:
            return current
        tm_packet = 2147483647
        tm_flush = _itimediff(ts_flush, current)
        for seg in self.snd_buf:
            diff = _itimediff(seg.resendts, current)
            if diff <= 0:
                return current
            if diff < tm_packet:
                tm_packet = diff
        minimal = min(tm_packet, tm_flush)
        if minimal >= self.interval:
            minimal = self.interval
        return current + minimal

    # 设置 MTU，并据此重算 MSS（MSS = MTU - IKCP_OVERHEAD）。
    def setmtu(self, mtu):
        if mtu < 50 or mtu < IKCP_OVERHEAD:
            min_str = str(max(50, IKCP_OVERHEAD))
            raise BufferError('MtU must be greater than' + min_str)
        self.mtu = uint32(mtu)
        self.mss = self.mtu - uint32(IKCP_OVERHEAD)
        self.buffer = bytearray((self.mtu + IKCP_OVERHEAD) * 3)
        self.buflen = 0

    # 设置发送/接收窗口大小。本仓库默认 (256, 256)，由 config.toml
    # [server].kcp_sndwnd / kcp_rcvwnd 控制。注意 rcvwnd 有下限 IKCP_WND_RCV(256)，
    # 调小不会生效。
    def wndsize(self, sndwnd, rcvwnd):
        if sndwnd > 0:
            self.snd_wnd = uint32(sndwnd)
        if rcvwnd > 0:
            self.rcv_wnd = uint32(max(rcvwnd, IKCP_WND_RCV))

    def waitsnd(self):
        return len(self.snd_buf) + len(self.snd_queue)

    # 快速模式：nodelay(nodly, interval, resend, nc) 表示 (开启 nodelay, 内部
    # update 间隔 ms, 快重传次数, 关闭拥塞控制)。本仓库会话使用 (1, 10, 2, 1)，
    # 即 10ms 间隔；ppSR 用 (1, 40, 2, 1)，echium 用 (1, 10, 2, 1)，见 docs/design/01 §3.3。
    def nodelay(self, nodly, interval, resend, nc):
        """
        fastest: nodelay(1, 20, 2, 1)
        nodly: 0:disable(default), 1:enable
        interval: internal update timer interval in millisec, default is 100ms
        resend: 0:disable fast resend(default), 1:enable fast resend
        nc: 0:normal congestion control(default), 1:disable congestion control
        """
        if nodly >= 0:
            self.nodly = nodly
            if nodly != 0:
                self.rx_minrto = int32(IKCP_RTO_NDL)
            else:
                self.rx_minrto = int32(IKCP_RTO_MIN)

        if interval >= 0:
            if interval > 5000:
                interval = 5000
            elif interval < 10:
                interval = 10
            self.interval = uint32(interval)

        if resend >= 0:
            self.fastresend = resend

        if nc >= 0:
            self.nocwnd = nc

    @staticmethod
    # 从收到的包头上取 conv（会话号），不做任何校验。
    def getconv(data):
        return (data[7] << 56 | data[6] << 48 | data[5] << 40 | data[4] << 32 | data[3] << 24 | data[2] << 16 | data[1] << 8 | data[0] << 0) & 0xFFFFFFFF

