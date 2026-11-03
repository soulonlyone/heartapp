"""heartapp —— 从智能手表（BLE 标准心率服务）实时读取心率，按 PC 会话记录。

典型用法::

    from heartapp.store import HeartRateStore
    from heartapp.engine import Engine

    store = HeartRateStore("heartrate.db")
    engine = Engine(store)
    engine.start()
    engine.connect_simulator("gaming")     # 或 engine.connect_device("你的手表名称")
    engine.start_pc_recording("晚间排位", "游戏")
    engine.mark_event("开局")
    ...
    engine.stop_recording()
    engine.shutdown()
"""

from __future__ import annotations

__version__ = "1.3.1"
APP_NAME = "心率记录仪"
APP_NAME_EN = "HeartApp"

__all__ = ["__version__", "APP_NAME", "APP_NAME_EN"]
