"""内置浏览器控制 —— 桌面端渲染进程与引擎之间的传输层。

状态本身在 :mod:`nono_agent.browser.bridge`（引擎侧），这里只负责把总线
暴露成 HTTP：一条常驻 SSE 推命令，一个 POST 收回执。
"""
