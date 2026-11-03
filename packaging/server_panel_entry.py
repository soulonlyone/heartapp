"""独立 Windows 面板入口；无界面服务仍使用 relay_entry.py。"""
from heartapp.ui.qt.server_panel import main

if __name__ == "__main__":
    raise SystemExit(main())
