"""Verify a proposed PyInstaller exclude list is genuinely unreachable at runtime.

The startup chain is not the whole story: MCP SDK / provider SDKs are imported lazily,
so they only show up when a user actually configures an MCP server or an OpenAI /
Anthropic provider. Every scenario below must be exercised, otherwise a "safe" exclude
list can still delete a module that only loads on a user's machine.

Usage:  uv run python scripts/probe_sidecar_deps.py
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Sequence

#: 场景名 -> 该场景需要 import 的模块。
SCENARIOS: dict[str, tuple[str, ...]] = {
    "startup": ("nono_app_server.app",),
    "sidecar_main": ("nono_app_server.main",),
    "mcp_client": ("nono_app_server.app", "nono_agent.mcp.client", "nono_agent.mcp.bridge"),
    "mcp_runtime": ("nono_app_server.app", "nono_agent.mcp.runtime"),
    "mcp_launcher": ("nono_app_server.app", "nono_agent.mcp.launcher"),
    "mcp_runtimes": ("nono_app_server.app", "nono_agent.mcp.runtimes"),
    "toolplugins_runtime": ("nono_app_server.app", "nono_agent.toolplugins.runtime"),
    "mcp_server": ("nono_app_server.app", "nono_agent.mcp.bridge"),
    "openai_provider": ("nono_app_server.app", "openai"),
    "anthropic_provider": ("nono_app_server.app", "anthropic"),
    "tiktoken": ("nono_app_server.app", "tiktoken"),
    "winpty": ("nono_app_server.app", "winpty", "winpty.winpty"),
}

#: 想剔除的顶层包，以及它们在 sys.modules 里出现的前缀。
WATCH: tuple[str, ...] = (
    "cryptography",
    "mypy",
    "mcp.server",
    "mcp.cli",
    "pytest",
    "setuptools",
    "pip",
    "numpy",
    "pandas",
    "IPython",
)


def run_scenario(name: str, modules: Sequence[str]) -> set[str]:
    code = (
        "import sys\n"
        f"for _m in {list(modules)!r}:\n"
        "    __import__(_m)\n"
        f"watch = {list(WATCH)!r}\n"
        "hit = sorted({m for m in sys.modules if any(m == w or m.startswith(w + '.') for w in watch)})\n"
        "print('\\n'.join(hit))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO
    )
    if out.returncode != 0:
        print(f"  !! {name} 场景 import 失败:\n{out.stderr.strip()[-600:]}")
        return {"<IMPORT FAILED>"}
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


def main() -> None:
    offenders: dict[str, set[str]] = {}
    print("场景 -> 是否触及候选剔除包\n")
    for name, modules in SCENARIOS.items():
        hits = run_scenario(name, modules)
        risky = {h for h in hits if not h.startswith("<")}
        print(f"{name:<20} {'-> ' + ', '.join(sorted(risky)) if risky else '(未触及)'}")
        if risky:
            offenders[name] = risky

    print()
    if offenders:
        print("!! 以下候选被触及，不能排除：")
        for name, hits in offenders.items():
            print(f"   {name}: {', '.join(sorted(hits))}")
    else:
        print("OK 所有场景均未触及候选剔除包")


if __name__ == "__main__":
    main()
