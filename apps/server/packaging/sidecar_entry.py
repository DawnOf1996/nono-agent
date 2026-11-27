"""PyInstaller 入口：冻结后直接跑 sidecar。"""

from __future__ import annotations

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from nono_app_server.main import main

    main()
