"""stdio 计算器 MCP —— 供 nono-agent 按 command/args 原样拉起。"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("calculator")


@server.tool()
def add(a: float, b: float) -> str:
    """Add two numbers and return the sum."""
    return str(a + b)


@server.tool()
def subtract(a: float, b: float) -> str:
    """Subtract b from a."""
    return str(a - b)


@server.tool()
def multiply(a: float, b: float) -> str:
    """Multiply two numbers."""
    return str(a * b)


@server.tool()
def divide(a: float, b: float) -> str:
    """Divide a by b. Rejects division by zero."""
    if b == 0:
        raise ValueError("division by zero")
    return str(a / b)


if __name__ == "__main__":
    server.run(transport="stdio")
