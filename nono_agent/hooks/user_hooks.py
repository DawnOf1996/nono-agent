"""用户态 Hooks —— 可落盘 hooks.json 加载与命令执行（对标 CC hooks）。

与引擎内 ``MiddlewareChain`` 区分：本模块是**可落盘的用户配置**，
执行器为子进程命令（HTTP 执行器与 SSRF 防护为后续项）。

事件：``session_start`` / ``session_end`` / ``before_tool`` / ``after_tool`` /
``before_submit`` / ``on_stop``。

hooks.json 格式::

    {
        "hooks": [
            {"event": "before_tool", "matcher": "write_file", "command": "my-hook"},
            {"event": "after_tool", "matcher": ".*", "command": "log-tool"}
        ]
    }

约定：命令通过 stdin 接收 JSON 上下文；``before_tool`` 钩子以非零退出码
否决本次工具调用。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nono_agent.permissions.decision import PermissionDecision
from nono_agent.utils.process import hidden_kwargs


@dataclass
class UserHook:
    """单条用户钩子。"""

    event: str                          # 事件名
    command: str                        # 子进程命令（shell）
    matcher: str | None = None          # 工具名正则（tool 事件用）
    timeout: int = 30                   # 超时（秒）
    _matcher_re: re.Pattern[str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.matcher is not None:
            try:
                self._matcher_re = re.compile(self.matcher)
            except re.error:
                self._matcher_re = None

    def matches(self, tool_name: str | None) -> bool:
        if self.matcher is None or self._matcher_re is None:
            return True
        if tool_name is None:
            return False
        return bool(self._matcher_re.search(tool_name))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserHook:
        return cls(
            event=str(data["event"]),
            command=str(data["command"]),
            matcher=data.get("matcher"),
            timeout=int(data.get("timeout", 30)),
        )


@dataclass
class UserHooksConfig:
    """用户钩子配置集合。"""

    hooks: list[UserHook] = field(default_factory=list)

    def for_event(self, event: str) -> list[UserHook]:
        return [h for h in self.hooks if h.event == event]
    @classmethod
    def from_file(cls, path: str | Path) -> UserHooksConfig:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        hooks_raw = data.get("hooks", []) if isinstance(data, dict) else []
        hooks: list[UserHook] = []
        for item in hooks_raw:
            if isinstance(item, dict) and "event" in item and "command" in item:
                try:
                    hooks.append(UserHook.from_dict(item))
                except (KeyError, ValueError):
                    continue
        return cls(hooks=hooks)


@dataclass
class HookRunResult:
    """单条钩子的执行结果。"""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class BeforeToolVerdict:
    """before_tool 钩子的汇总判定。

    三态：ALLOW（不否决）/ ASK（必须问）/ DENY（拒绝）。

    .. important::
       这里的 ALLOW 语义是"**不否决**"，不是"直接放行"。hook 与权限检查链
       是**串联**的：hook 说允许，请求仍要继续走完整条链（安全底线、用户规则、
       权限模式都在后面）。业务方能加规则，但不能拆规则。
    """

    decision: PermissionDecision = PermissionDecision.ALLOW
    reason: str | None = None
    rule: str | None = None
    results: list[HookRunResult] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """兼容旧接口 —— 只有 ALLOW 才算"没被否决"。"""
        return self.decision is PermissionDecision.ALLOW


class UserHookExecutor:
    """执行用户钩子命令。"""

    def __init__(self, config: UserHooksConfig) -> None:
        self._config = config
    async def run(
        self,
        hook: UserHook,
        context: dict[str, Any],
    ) -> HookRunResult:
        """运行单条钩子命令，把上下文以 JSON 传给 stdin。"""
        try:
            proc = await asyncio.create_subprocess_shell(
                hook.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **hidden_kwargs(),
            )
        except OSError as exc:
            return HookRunResult(exit_code=-1, stdout="", stderr=str(exc))

        payload = json.dumps(context, ensure_ascii=False)
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(payload.encode("utf-8")), timeout=hook.timeout
            )
            return HookRunResult(
                exit_code=proc.returncode if proc.returncode is not None else -1,
                stdout=(stdout_b or b"").decode("utf-8", errors="replace"),
                stderr=(stderr_b or b"").decode("utf-8", errors="replace"),
            )
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.communicate()
            return HookRunResult(exit_code=-1, stdout="", stderr="timed out", timed_out=True)

    async def run_event(self, event: str, context: dict[str, Any]) -> list[HookRunResult]:
        """运行某事件的所有钩子（不过滤 matcher，由调用方决定）。"""
        results: list[HookRunResult] = []
        for hook in self._config.for_event(event):
            results.append(await self.run(hook, context))
        return results

    async def check_before_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: dict[str, Any],
    ) -> BeforeToolVerdict:
        """运行 before_tool 钩子。

        判定规则：

        - 退出码非零 → **DENY**（不可否决的硬拒）
        - 退出码为零且 stdout 是 JSON 且带 ``decision`` → 按它来
          （``ask`` 表示"必须问"，``deny`` 表示"拒绝"）
        - 退出码为零、无结构化输出 → **ALLOW**（不否决，继续走权限检查链）
        """
        results: list[HookRunResult] = []
        ctx = {**context, "tool_name": tool_name, "tool_input": tool_input}
        for hook in self._config.for_event("before_tool"):
            if not hook.matches(tool_name):
                continue
            result = await self.run(hook, ctx)
            results.append(result)
            if result.exit_code != 0:
                reason = result.stderr.strip() or result.stdout.strip() or "vetoed by hook"
                return BeforeToolVerdict(
                    decision=PermissionDecision.DENY,
                    reason=reason,
                    results=results,
                )
            verdict = _parse_verdict(result.stdout)
            if verdict is not None and verdict.decision is not PermissionDecision.ALLOW:
                verdict.results = results
                return verdict
        return BeforeToolVerdict(
            decision=PermissionDecision.ALLOW,
            results=results,
        )


def _parse_verdict(stdout: str) -> BeforeToolVerdict | None:
    """从 hook 的 stdout 解析结构化判定。

    约定：stdout 是一个 JSON 对象，可带 ``decision``（``allow`` / ``deny`` /
    ``ask``）、``reason``、``rule``。解析不出来当作"没有意见"。
    """
    text = (stdout or "").strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("decision")
    if not isinstance(raw, str):
        return None
    try:
        decision = PermissionDecision(raw.strip().lower())
    except ValueError:
        return None
    reason = data.get("reason")
    rule = data.get("rule")
    return BeforeToolVerdict(
        decision=decision,
        reason=reason if isinstance(reason, str) else None,
        rule=rule if isinstance(rule, str) else None,
    )


def load_user_hook_executor(cwd: str | Path) -> UserHookExecutor:
    """按工作区加载 ``hooks.json``，返回执行器。

    没有配置文件就返回空执行器 —— 链上那一步直接跳过，不影响其它检查。
    """
    from nono_agent.paths import project_dir

    config = UserHooksConfig.from_file(project_dir(cwd) / "hooks.json")
    return UserHookExecutor(config)
