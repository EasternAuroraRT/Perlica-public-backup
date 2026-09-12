# loopback_files.py
"""通过回环网络或指定网卡把本地文件分享出去的文件服务器。

对外接口：
    server = FileServer(host="0.0.0.0", allow_non_loopback=True,
                        advertise_host="host.docker.internal").start()
    url = server.publish("/path/to/file")   # 拿到就能用

关键概念：
    host            真正绑定的网卡地址（决定谁能连进来）
    advertise_host  生成链接时写进 URL 的地址（决定链接里显示什么）
    这两个分开，绑 0.0.0.0 时链接才不会是一串没法访问的 0.0.0.0。

每个 publish 链接都是密码学随机、一次性的。链接带有“滑动过期”：
只要在 ttl（默认 60 秒）内被访问过就自动续期，静默超过 ttl 未被访问
即自动失效并清理。链接 URL 里的文件名默认也会被随机化，避免从链接
推断出真实文件名。
"""

from __future__ import annotations

import functools
import ipaddress
import mimetypes
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Union

__all__ = [
    "FileServer",
    "publish",
    "shutdown_default_server",
]

_CHUNK_SIZE = 64 * 1024
_URL_PREFIX = "/f/"


# --------------------------------------------------------------------------
# 辅助结构
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Entry:
    path: str          # 磁盘上的绝对路径
    name: str          # 下载时显示的文件名
    disposition: str   # "attachment" 或 "inline"


@dataclass
class _Item:
    entry: _Entry
    expires_at: float  # time.monotonic() 的截止时刻，inf 表示永不过期


class _Registry:
    """token -> _Entry 的线程安全映射，支持滑动过期（idle TTL）。

    ttl 为 None 时永不过期；否则每次成功 get() 都会把过期时间续到
    now + ttl，静默超过 ttl 未被访问即失效。
    """

    def __init__(self, ttl: Optional[float] = None) -> None:
        self._lock = threading.Lock()
        self._ttl = ttl
        self._items: Dict[str, _Item] = {}

    def _deadline(self) -> float:
        if self._ttl is None:
            return float("inf")
        return time.monotonic() + self._ttl

    @staticmethod
    def _expired(item: _Item) -> bool:
        return time.monotonic() > item.expires_at

    def add(self, token: str, entry: _Entry) -> None:
        with self._lock:
            self._items[token] = _Item(entry, self._deadline())

    def get(self, token: str) -> Optional[_Entry]:
        with self._lock:
            item = self._items.get(token)
            if item is None:
                return None
            if self._expired(item):
                self._items.pop(token, None)
                return None
            item.expires_at = self._deadline()  # 访问即续期
            return item.entry

    def pop(self, token: str) -> bool:
        with self._lock:
            return self._items.pop(token, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def purge(self) -> None:
        """清理已过期的条目。"""
        with self._lock:
            dead = [t for t, it in self._items.items() if self._expired(it)]
            for token in dead:
                self._items.pop(token, None)


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h in ("localhost", "::1", "0:0:0:0:0:0:0:1"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _parse_range(header: Optional[str], size: int):
    """解析 Range 头。返回 None / 'invalid' / (start, end)。"""
    if not header:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None
    spec = header[6:].split(",")[0].strip()
    start_s, sep, end_s = spec.partition("-")
    if not sep:
        return "invalid"
    start_s, end_s = start_s.strip(), end_s.strip()
    try:
        if not start_s:
            if not end_s:
                return "invalid"
            suffix = int(end_s)
            if suffix <= 0 or size == 0:
                return "invalid"
            start, end = max(0, size - suffix), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start < 0 or end < start or start >= size:
        return "invalid"
    return start, min(end, size - 1)


def _content_disposition(entry: _Entry) -> str:
    ascii_name = entry.name.encode("ascii", "ignore").decode("ascii") or "file"
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    quoted = urllib.parse.quote(entry.name, safe="")
    return (
        f'{entry.disposition}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quoted}"
    )


# --------------------------------------------------------------------------
# HTTP 处理器
# --------------------------------------------------------------------------
class _FileRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "LoopbackFileServer/1.0"
    sys_version = ""

    def __init__(self, *args, registry: _Registry, logger=None, **kwargs):
        self._registry = registry
        self._logger = logger
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self._serve(head_only=False)

    def do_HEAD(self):
        self._serve(head_only=True)

    def _lookup(self) -> Optional[_Entry]:
        path = urllib.parse.urlsplit(self.path).path
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2 or parts[0] != _URL_PREFIX.strip("/"):
            return None
        return self._registry.get(parts[1])

    def _serve(self, head_only: bool) -> None:
        entry = self._lookup()
        if entry is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Not Found", head_only)
            return

        try:
            fp = open(entry.path, "rb")
        except OSError:
            self._send_error(HTTPStatus.NOT_FOUND, "文件已不可用", head_only)
            return

        with fp:
            st = os.fstat(fp.fileno())
            size = st.st_size
            ctype = (
                mimetypes.guess_type(entry.name)[0] or "application/octet-stream"
            )

            rng = _parse_range(self.headers.get("Range"), size)
            if rng == "invalid":
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if rng is None:
                start, end, status = 0, size - 1, HTTPStatus.OK
            else:
                start, end = rng
                status = HTTPStatus.PARTIAL_CONTENT
            length = max(0, end - start + 1) if size else 0

            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition", _content_disposition(entry))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Last-Modified", self.date_time_string(st.st_mtime))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()

            if head_only or length == 0:
                return

            fp.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fp.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
                    return
                remaining -= len(chunk)

    def _send_error(self, status, message: str, head_only: bool = False) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def log_message(self, format: str, *args) -> None:
        if self._logger is not None:
            self._logger("%s - %s" % (self.address_string(), format % args))


# --------------------------------------------------------------------------
# 对外的服务器类
# --------------------------------------------------------------------------
class FileServer:
    """文件服务器。

    参数
    ----
    host : 真正绑定的网卡地址。
        "127.0.0.1"（默认）只有宿主机本机进程能连；
        "0.0.0.0" 监听所有网卡，容器/局域网都能连，需 allow_non_loopback=True。
    port : 监听端口，0 表示由系统分配空闲端口。
    advertise_host : 生成链接时写进 URL 的 host，默认等于 host。
        当 host 是 "0.0.0.0" 时，务必显式传一个容器真正能解析的地址，
        否则链接里是一串没法访问的 0.0.0.0。
        常见取值：
          - Docker Desktop (Mac/Win)： "host.docker.internal"
          - Linux 默认 bridge：        docker0 的 IP，通常 "172.17.0.1"
          - 容器用 --network host：    "127.0.0.1"
          - 局域网其它机器访问：        宿主机的局域网 IP
    token_bytes : 每个文件随机 token 的字节数（默认 16）。
    ttl : 链接的空闲存活秒数（默认 60）。在 ttl 内被访问会自动续期，
          静默超过 ttl 未访问即失效；传 None 表示永不过期。
    obscure_name : 是否把链接里的文件名也随机化（默认 True），
          避免从 URL 推断真实文件名。
    logger : 可选日志回调 logger(str)，None 表示静默。
    allow_non_loopback : 允许绑定非回环地址（默认 False，安全兜底）。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        advertise_host: Optional[str] = None,
        token_bytes: int = 16,
        ttl: Optional[float] = 60.0,
        obscure_name: bool = True,
        logger: Optional[Callable[[str], None]] = None,
        allow_non_loopback: bool = False,
    ) -> None:
        if not allow_non_loopback and not _is_loopback_host(host):
            raise ValueError(
                f"host={host!r} 不是回环地址。"
                "如确需对外监听，请显式传入 allow_non_loopback=True。"
            )
        if token_bytes < 8:
            raise ValueError("token_bytes 至少为 8，否则链接容易被猜测")
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl 必须为正数，或传 None 表示永不过期")

        self._host = host
        self._port = int(port)
        self._advertise_host = advertise_host or host
        self._token_bytes = int(token_bytes)
        self._ttl = ttl
        self._obscure_name = bool(obscure_name)
        self._logger = logger
        self._registry = _Registry(ttl)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._sweeper: Optional[threading.Thread] = None
        self._sweeper_stop: Optional[threading.Event] = None
        self._start_lock = threading.Lock()

    # ---------------- 生命周期 ----------------
    def start(self) -> "FileServer":
        """启动服务（幂等）。返回 self，可链式调用。"""
        with self._start_lock:
            if self._httpd is not None:
                return self

            handler = functools.partial(
                _FileRequestHandler,
                registry=self._registry,
                logger=self._logger,
            )
            try:
                httpd = ThreadingHTTPServer((self._host, self._port), handler)
            except OSError as exc:
                hint = ""
                if self._host not in ("0.0.0.0", "::"):
                    hint = (
                        f"\n提示：{self._host} 可能不是本机拥有的地址。"
                        "如果想监听所有网卡，host 用 '0.0.0.0' 并传 allow_non_loopback=True。"
                    )
                raise RuntimeError(
                    f"无法在 {self._host}:{self._port} 上启动文件服务器: {exc}{hint}"
                ) from exc

            httpd.daemon_threads = True
            self._httpd = httpd
            self._thread = threading.Thread(
                target=httpd.serve_forever,
                name="loopback-file-server",
                daemon=True,
            )
            self._thread.start()

            if self._ttl is not None:
                self._sweeper_stop = threading.Event()
                self._sweeper = threading.Thread(
                    target=self._sweep_expired,
                    name="loopback-file-server-expirer",
                    daemon=True,
                )
                self._sweeper.start()
            return self

    def stop(self, timeout: float = 5.0) -> None:
        """停止服务并释放端口（幂等）。"""
        with self._start_lock:
            httpd, thread = self._httpd, self._thread
            sweeper, self._sweeper = self._sweeper, None
            sweeper_stop, self._sweeper_stop = self._sweeper_stop, None
            self._httpd, self._thread = None, None
        if sweeper_stop is not None:
            sweeper_stop.set()
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout)
        if sweeper is not None:
            sweeper.join(timeout)

    def _sweep_expired(self) -> None:
        stop, ttl = self._sweeper_stop, self._ttl
        if stop is None or ttl is None:
            return
        interval = min(max(ttl / 4.0, 1.0), 15.0)
        while not stop.wait(interval):
            self._registry.purge()

    def __enter__(self) -> "FileServer":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---------------- 对外接口 ----------------
    def publish(
        self,
        path: Union[str, os.PathLike],
        *,
        name: Optional[str] = None,
        disposition: str = "attachment",
        advertise_host: Optional[str] = None,
    ) -> str:
        """发布文件，返回可以直接拿来访问的链接。

        参数
        ----
        path : 本地文件路径（相对路径按当前工作目录解析）。
        name : 下载时显示的文件名，默认用磁盘上的文件名。
        disposition : "attachment"（默认，浏览器下载）
                      或 "inline"（浏览器内打开，比如 PDF、图片）。
        advertise_host : 临时覆盖实例级的 advertise_host，
                         用于一次性生成给不同网络用的链接。

        返回
        ----
        形如 http://<advertise_host>:<port>/f/<token>/<random> 的 URL。
        token 与文件名段都是密码学随机的一次性值，无法从链接推断内容。
        """
        self.start()

        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"文件不存在或不是普通文件: {path}")
        p = p.resolve()

        if disposition not in ("attachment", "inline"):
            raise ValueError('disposition 只能是 "attachment" 或 "inline"')

        token = secrets.token_urlsafe(self._token_bytes)
        entry = _Entry(str(p), name or p.name, disposition)
        self._registry.add(token, entry)
        host = advertise_host or self._advertise_host
        url_name = secrets.token_urlsafe(6) if self._obscure_name else entry.name
        return self._make_url(host, token, url_name)

    register = publish

    def unpublish(self, token_or_url: str) -> bool:
        """撤销链接。参数可以是 token，也可以是 publish 返回的完整 URL。"""
        token = self._extract_token(token_or_url)
        return self._registry.pop(token) if token else False

    revoke = unpublish

    def unpublish_all(self) -> None:
        """撤销当前所有链接（不停止服务）。"""
        self._registry.clear()

    # ---------------- 属性 ----------------
    @property
    def host(self) -> str:
        return self._host

    @property
    def advertise_host(self) -> str:
        return self._advertise_host

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self._port
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return self._make_base(self._advertise_host)

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def __repr__(self) -> str:
        state = "running" if self.running else "stopped"
        return (
            f"<FileServer bind={self._host}:{self.port} "
            f"advertise={self._advertise_host} ({state})>"
        )

    # ---------------- 内部工具 ----------------
    def _make_base(self, host: str) -> str:
        if ":" in host and not host.startswith("["):  # IPv6
            host = f"[{host}]"
        return f"http://{host}:{self.port}"

    def _make_url(self, host: str, token: str, filename: str) -> str:
        return (
            f"{self._make_base(host)}{_URL_PREFIX}{token}/"
            f"{urllib.parse.quote(filename, safe='')}"
        )

    @staticmethod
    def _extract_token(token_or_url: str) -> Optional[str]:
        if _URL_PREFIX in token_or_url:
            tail = token_or_url.split(_URL_PREFIX, 1)[1]
            return tail.split("/", 1)[0] or None
        token = token_or_url.strip()
        return token or None


# --------------------------------------------------------------------------
# 模块级便捷接口：进程内共享一个默认服务器
# --------------------------------------------------------------------------
_default_server: Optional[FileServer] = None
_default_lock = threading.Lock()


def _get_default_server() -> FileServer:
    global _default_server
    with _default_lock:
        if _default_server is None:
            _default_server = FileServer(
                                host="0.0.0.0",
                                allow_non_loopback=True,
                                advertise_host="host.docker.internal",
                            ).start()
        return _default_server


def publish(path: Union[str, os.PathLike], **kwargs) -> str:
    """用进程内共享的默认服务器发布文件。

    默认服务器只监听回环地址；要对外，请自己 new 一个 FileServer。
    """
    return _get_default_server().publish(path, **kwargs)


def shutdown_default_server() -> None:
    """停止默认服务器（如果已启动）。"""
    global _default_server
    with _default_lock:
        server, _default_server = _default_server, None
    if server is not None:
        server.stop()


# --------------------------------------------------------------------------
# 命令行演示：python loopback_files.py [文件路径] [--docker]
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import time

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    target = args[0] if args else __file__

    if "--docker" in flags:
        # Docker Desktop 场景：所有网卡 + host.docker.internal
        server = FileServer(
            host="0.0.0.0",
            allow_non_loopback=True,
            advertise_host="host.docker.internal",
            logger=print,
        )
    else:
        server = FileServer(logger=print)

    with server:
        link = server.publish(target)
        print(f"已发布: {target}")
        print(f"下载链接: {link}")
        print("按 Ctrl+C 退出…")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在关闭…")