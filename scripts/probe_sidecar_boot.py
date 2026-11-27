"""Sidecar 启动耗时探针 —— 定位「桌面端首屏黑屏」的钱花在哪。

用法（在仓库根目录）::

    uv run python scripts/probe_sidecar_boot.py            # 全部
    uv run python scripts/probe_sidecar_boot.py --only boot,port
    uv run python scripts/probe_sidecar_boot.py --repeat 5

分四段测，对应四个独立的优化点：

``port``  ``main.py`` 启动前的「端口是否被占用」预检。
``boot``  进程 spawn 到 ``/health`` 200 的端到端时间。
``phases`` 启动各阶段插桩，看 import / create_app / 端口预检各占多少。
``floor`` 把官方 MCP SDK 从导入链上摘掉，量 P0 的收益上限。

背景（本仓库实测，Windows + dev 模式）：

* ``socket.connect_ex`` 连一个**空闲**回环端口要约 2.0s（本机装了会拦截回环流量的
  WFP filter），连一个**有监听**的端口则是 0ms。而 Node 侧不受影响——libuv 在 Windows
  上走 ``ConnectEx`` + ``SIO_LOOPBACK_FAST_PATH``，绕开了这层检查。
* 官方 MCP SDK（``import mcp``）单独就要 1.0s，但 sidecar 启动只需要它的
  ``load_user_mcp_config`` / ``collect_mcp_servers`` 这类纯函数。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _python() -> str:
    return sys.executable or VENV_PYTHON


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values)) if values else None


def _child_env(port: int) -> dict[str, str]:
    env = dict(os.environ)
    env["NONO_PORT"] = str(port)
    env["NONO_HOST"] = "127.0.0.1"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("NONO_WEB_DIST", None)  # 别去托管 web dist，测的是 sidecar 本身
    return env


# --------------------------------------------------------------------------- #
# 1. port pre-check
# --------------------------------------------------------------------------- #


def probe_port(repeat: int, port: int) -> dict[str, object]:
    """对比 connect_ex 与 bind 两种「端口是否空闲」的判法。"""
    rows: list[tuple[str, Callable[[], object], int]] = []

    def connect_ex() -> object:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            return s.connect_ex(("127.0.0.1", port))
        finally:
            s.close()

    def bind() -> object:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return "free"
        except OSError as exc:
            return f"taken ({exc.strerror})"
        finally:
            s.close()

    def listener() -> object:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("127.0.0.1", port))
            return "connected"
        except OSError as exc:
            return f"refused ({exc.__class__.__name__})"
        finally:
            s.close()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", port))
    srv.listen(8)
    rows.append(("connect_ex -> 有监听端口", listener, repeat))
    rows.append(("connect_ex -> 空闲端口", connect_ex, repeat))
    srv.close()
    rows.append(("bind     -> 空闲端口", bind, repeat))

    print(f"\n[port] 端口空闲判法（端口 {port}）")
    results: dict[str, object] = {}
    for label, fn, n in rows:
        samples = []
        for _ in range(n):
            t0 = time.perf_counter()
            out = fn()
            samples.append((time.perf_counter() - t0) * 1000)
        results[label] = {"median_ms": _median(samples), "result": out}
        print(f"  {label:<28} median={_median(samples):>6} ms   ({out})")
    return results


# --------------------------------------------------------------------------- #
# 2. end-to-end boot
# --------------------------------------------------------------------------- #


def probe_boot(repeat: int, base_port: int, timeout: float) -> dict[str, object]:
    """spawn sidecar 到 /health 200 的端到端时间。"""
    samples: list[float] = []
    print(f"\n[boot] 端到端（spawn -> /health 200），{repeat} 次")
    for i in range(repeat):
        port = base_port + i
        proc = subprocess.Popen(
            [_python(), "-m", "nono_app_server"],
            cwd=REPO_ROOT,
            env=_child_env(port),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        url = f"http://127.0.0.1:{port}/health"
        t0 = time.perf_counter()
        ready_ms: float | None = None
        try:
            while time.perf_counter() - t0 < timeout:
                if proc.poll() is not None:
                    raise RuntimeError(f"sidecar 提前退出，code={proc.returncode}")
                try:
                    with urllib.request.urlopen(url, timeout=0.5) as resp:
                        if resp.status == 200:
                            ready_ms = (time.perf_counter() - t0) * 1000
                            break
                except (urllib.error.URLError, OSError):
                    time.sleep(0.02)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if ready_ms is None:
            raise RuntimeError(f"{timeout:.0f}s 内 /health 未就绪")
        samples.append(ready_ms)
        print(f"  run {i + 1}: {ready_ms:.0f} ms")
    summary = {
        "runs": len(samples),
        "min_ms": round(min(samples)),
        "median_ms": _median(samples),
        "max_ms": round(max(samples)),
    }
    print(f"  -> {json.dumps(summary, ensure_ascii=False)}")
    return summary


# --------------------------------------------------------------------------- #
# 3. import time
# --------------------------------------------------------------------------- #


def probe_import() -> dict[str, object]:
    code = (
        "import time, json, sys;"
        "t=time.perf_counter();"
        "import nono_app_server.app;"
        "dt=(time.perf_counter()-t)*1000;"
        "print(json.dumps({'import_ms': round(dt), 'modules': len(sys.modules),"
        "'mcp_loaded': 'mcp' in sys.modules}))"
    )
    out = subprocess.run([_python(), "-c", code], cwd=REPO_ROOT, capture_output=True, text=True)
    line = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "{}"
    data = json.loads(line)
    print(f"\n[import] import nono_app_server.app -> {json.dumps(data, ensure_ascii=False)}")
    return data


# --------------------------------------------------------------------------- #
# 4. MCP SDK floor
# --------------------------------------------------------------------------- #


def probe_floor() -> dict[str, object]:
    """SDK 不在启动链上时的导入下限 = P0 能拿到的全部收益。"""
    stub = """
import sys, types, time, json

def mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

sse = mod('mcp.client.sse', sse_client=object)
stdio = mod('mcp.client.stdio', StdioServerParameters=object, stdio_client=object,
            get_default_environment=dict)
sh = mod('mcp.client.streamable_http', streamable_http_client=object)
client = mod('mcp.client', sse=sse, stdio=stdio)
exc = mod('mcp.shared.exceptions', MCPError=Exception)
shared = mod('mcp.shared', exceptions=exc)
mcp = mod('mcp', Client=object)
mcp.client, mcp.shared = client, shared

t = time.perf_counter()
import nono_app_server.app
dt = (time.perf_counter() - t) * 1000
print(json.dumps({'import_ms': round(dt), 'modules': len(sys.modules)}))
"""
    out = subprocess.run([_python(), "-c", stub], cwd=REPO_ROOT, capture_output=True, text=True)
    line = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "{}"
    data = json.loads(line)
    print(f"\n[floor] 摘掉 MCP SDK 后的导入 -> {json.dumps(data, ensure_ascii=False)}")
    return data


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

ALL: dict[str, Callable[..., dict[str, object]]] = {
    "port": probe_port,
    "boot": probe_boot,
    "import": probe_import,
    "floor": probe_floor,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--only", default="all", help="逗号分隔：port,boot,import,floor（默认 all）")
    ap.add_argument("--repeat", type=int, default=3, help="boot/port 的重复次数")
    ap.add_argument("--base-port", type=int, default=8940)
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args(argv)

    wanted = (
        list(ALL)
        if args.only == "all"
        else [s.strip() for s in args.only.split(",") if s.strip()]
    )
    unknown = [s for s in wanted if s not in ALL]
    if unknown:
        ap.error(f"未知的探针：{', '.join(unknown)}；可选：{', '.join(ALL)}")

    out: dict[str, object] = {}
    for name in wanted:
        if name == "port":
            out[name] = probe_port(args.repeat, args.base_port)
        elif name == "boot":
            out[name] = probe_boot(args.repeat, args.base_port, args.timeout)
        else:
            out[name] = ALL[name]()
        print()

    print("=" * 62)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
