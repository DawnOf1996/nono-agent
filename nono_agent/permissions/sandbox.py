"""路径沙箱与 Ignore 匹配 —— 拒绝路径逃逸，遵守 gitignore/nonoignore。

PathSandbox 把工具传入的（可能相对的、含 ``..`` 的）路径解析到工作区根目录，
并校验最终路径仍位于允许的根目录内，防止路径逃逸。

IgnoreMatcher 加载 ``.gitignore`` / ``.nonoignore``，供 Glob/Grep/Read 等工具
在列举与搜索时跳过忽略项。实现 gitignore 语义的一个子集：
- 以 ``/`` 结尾的匹配目录（含其下所有内容）
- ``**`` 递归通配；``*`` / ``?`` 段内通配
- ``#`` 注释；``!`` 前缀取反（un-ignore）
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path


class PathEscapesSandboxError(ValueError):
    """路径解析后逃逸出工作区根目录。"""


class PathSandbox:
    """工作区路径沙箱 —— 解析并校验路径不逃逸出允许根目录。

    Args:
        workspace_root: 工作区根目录（默认为 cwd）。所有相对路径基于此解析。
        extra_roots: 额外允许的根目录（如临时目录、全局配置目录）。
    """

    def __init__(
        self,
        workspace_root: str | Path,
        extra_roots: Iterable[str | Path] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._roots: list[Path] = [self.workspace_root]
        if extra_roots:
            self._roots.extend(Path(r).resolve() for r in extra_roots)

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    def resolve(self, path: str | Path) -> Path:
        """将（可能相对的）路径解析为绝对路径，但不校验逃逸。

        相对路径基于 workspace_root 解析；``~`` 展开为用户目录。
        """
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace_root / p
        return Path(os.path.expanduser(str(p))).resolve()

    def is_within(self, path: str | Path) -> bool:
        """判断解析后的路径是否位于任一允许根目录内。"""
        resolved = self.resolve(path)
        return any(self._is_relative_to(resolved, root) for root in self._roots)

    def validate(self, path: str | Path) -> Path:
        """解析路径并校验不逃逸出允许根目录。

        Returns:
            解析后的绝对路径。

        Raises:
            PathEscapesSandboxError: 路径逃逸出允许根目录。
        """
        resolved = self.resolve(path)
        if not self.is_within(resolved):
            raise PathEscapesSandboxError(
                f"Path {str(path)!r} escapes the workspace sandbox "
                f"(resolved to {str(resolved)!r})"
            )
        return resolved

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        """Python 3.9+ 兼容的 is_relative_to。"""
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False


class IgnoreMatcher:
    """Ignore 规则匹配器 —— 支持 gitignore 语义的子集。

    匹配基于相对于工作区根目录的路径。
    """

    def __init__(self, patterns: Iterable[str] | None = None) -> None:
        self._rules: list[tuple[bool, re.Pattern[str]]] = []  # (negated, regex)
        if patterns:
            self.add_patterns(patterns)

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def add_patterns(self, patterns: Iterable[str]) -> None:
        for line in patterns:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:].strip()
            if not line:
                continue
            regex = self._compile(line)
            if regex is not None:
                self._rules.append((negated, regex))

    @classmethod
    def from_file(cls, path: str | Path) -> IgnoreMatcher:
        """从单个 ignore 文件加载规则。文件不存在时返回空匹配器。"""
        p = Path(path)
        matcher = cls()
        if p.exists():
            matcher.add_patterns(p.read_text(encoding="utf-8", errors="replace").splitlines())
        return matcher

    @classmethod
    def from_workspace(cls, root: str | Path) -> IgnoreMatcher:
        """加载工作区根目录的 ``.gitignore`` 与 ``.nonoignore``。"""
        root_path = Path(root)
        matcher = cls()
        for name in (".gitignore", ".nonoignore"):
            matcher.add_patterns_from_file(root_path / name)
        return matcher

    def add_patterns_from_file(self, path: str | Path) -> None:
        p = Path(path)
        if p.exists():
            self.add_patterns(p.read_text(encoding="utf-8", errors="replace").splitlines())

    # ------------------------------------------------------------------
    # 匹配
    # ------------------------------------------------------------------

    def is_ignored(self, path: str | Path, *, is_dir: bool = False) -> bool:
        """判断路径是否被忽略。

        Args:
            path: 相对工作区的路径
            is_dir: 是否目录
        """
        p = str(path).replace("\\", "/").strip("./")
        if not p:
            return False

        ignored = False
        for negated, regex in self._rules:
            if regex.match(p):
                ignored = not negated
        return ignored

    # ------------------------------------------------------------------
    # 编译
    # ------------------------------------------------------------------

    @classmethod
    def _compile(cls, pattern: str) -> re.Pattern[str] | None:
        """把单条 gitignore 模式编译为正则。"""
        dir_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        if not pattern:
            return None

        anchored = pattern.startswith("/")
        if anchored:
            pattern = pattern[1:]

        segments = pattern.split("/")
        regex_parts: list[str] = []
        for seg in segments:
            if seg == "**":
                regex_parts.append("(?:[^/]+/)*")
            else:
                regex_parts.append(cls._glob_to_regex(seg))

        body = "/".join(regex_parts)
        if dir_only:
            core = body + "(?:/.*)?"
        else:
            core = body

        if anchored:
            regex = f"^{core}$"
        else:
            regex = f"(?:^|.*/){core}$"

        return re.compile(regex)

    @staticmethod
    def _glob_to_regex(seg: str) -> str:
        """把单个路径段内的 glob 转换为正则（``*`` / ``?`` / ``[...]``）。"""
        out: list[str] = []
        i = 0
        while i < len(seg):
            c = seg[i]
            if c == "*":
                out.append("[^/]*")
            elif c == "?":
                out.append("[^/]")
            elif c == "[":
                j = i + 1
                while j < len(seg) and seg[j] != "]":
                    j += 1
                if j < len(seg):
                    out.append(seg[i : j + 1])
                    i = j
                else:
                    out.append(re.escape(c))
            else:
                out.append(re.escape(c))
            i += 1
        return "".join(out)


def check_path_within(path: str | Path, root: str | Path) -> bool:
    """便捷函数 —— 判断路径是否在根目录内（无额外根）。"""
    sandbox = PathSandbox(root)
    return sandbox.is_within(path)
