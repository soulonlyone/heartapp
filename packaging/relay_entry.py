"""服务端独立入口：给 `HeartApp-Relay.exe` 用的包装。

等价于 `python -m heartapp.relay`，只是让 PyInstaller 有明确的入口脚本。
只依赖 websockets 和标准库，不会牵进 Qt / BLE / 数据库。
"""
from heartapp.relay import main

if __name__ == "__main__":
    main()
