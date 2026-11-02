"""权限拦截链的端到端冒烟 —— 用真实 bash 工具走完整链路。

不是单元测试，是把「设置 → 策略 → 拦截链 → 执行器」串起来跑一遍，
确认行为跟设计一致：

- `ls`（纯读）      → 放行，不打扰用户
- `echo > a.txt`    → 询问
- `rm -rf`          → 询问（不直接拒，用户保留话语权）
- deny 规则命中     → 拒绝
- 无交互通道        → 拒绝（fail-closed）
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from nono_agent.core.types import ToolUseBlock
from nono_agent.permissions import (
    PermissionGate,
    PermissionMode,
    PermissionPolicy,
    PermissionRule,
    RuleAction,
)
from nono_agent.permissions.policy import policy_from_settings
from nono_agent.settings import load_settings_from_env
from nono_agent.tools.builtin.bash import BashTool
from nono_agent.tools.context import ToolUseContext
from nono_agent.tools.executor import BatchToolExecutor
from nono_agent.tools.registry import ToolRegistry


async def _run(command: str, *, policy, checker=None, cwd=None):
    registry = ToolRegistry()
    registry.register(BashTool())
    gate = PermissionGate(policy=policy)
    ctx = ToolUseContext(
        cwd=str(cwd),
        policy=policy,
        gate=gate,
        permission_checker=checker,
    )
    blocks = [ToolUseBlock(id="t1", name="bash", input={"command": command})]
    results = await BatchToolExecutor(registry).execute(blocks, ctx)
    r = results[0]
    return ("DENIED" if r.permission_denied else "RAN"), r.result.content[:70]


async def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    settings_dir = tmp / ".nono-agent"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "mode": "default",
                    "rules": [
                        {
                            "action": "deny",
                            "tool_name": "bash",
                            "bash_pattern": "rm -rf",
                            "description": "禁止 rm -rf",
                            "id": "no-rm-rf",
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    policy = policy_from_settings(load_settings_from_env(tmp))
    print(f"载入的规则数：{len(policy.rules)}  模式：{policy.mode.value}")
    print(f"deny 规则：{policy.rules[0].label}\n")

    asked: list[str] = []

    def checker(tool_name, params):
        asked.append(params.get("command", ""))
        return False  # 用户一律拒绝

    cases = [
        ("ls -la", policy, checker, "放行"),
        ("echo hi > a.txt", policy, checker, "询问"),
        ("rm -rf /tmp/whatever", policy, checker, "拒绝（deny 规则）"),
    ]
    ok = True
    for cmd, pol, chk, expect in cases:
        status, detail = await _run(cmd, policy=pol, checker=chk, cwd=tmp)
        print(f"  {cmd!r:26} → {status:8} (期望 {expect})")
        print(f"      {detail}")

    # 无交互通道 → fail-closed
    status, detail = await _run("echo hi > b.txt", policy=policy, checker=None, cwd=tmp)
    print(f"\n  无交互通道：{status} (期望 DENIED)")
    ok = ok and status == "DENIED"

    # plan 模式：ls 能跑，写被拒
    plan = PermissionPolicy(mode=PermissionMode.PLAN)
    for cmd in ("ls -la", "echo hi > c.txt"):
        status, _ = await _run(cmd, policy=plan, checker=checker, cwd=tmp)
        print(f"  plan 模式 {cmd!r:20} → {status}")

    print(f"\n被询问的命令：{asked}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
