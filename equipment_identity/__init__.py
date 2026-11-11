"""跨站装备资料的采集、实体归并与证据链模块。

设计原则：原始观察与人工决定只追加、不覆盖；实体、候选、合成参数等
一切派生状态都是「原始记录 + 生效决定」的纯函数，因此可以从原始记录
随时重算，且结果不依赖导入顺序。
"""
from .store import Store
from .collector import collect
from .decisions import import_decisions, active_decisions
from .records import latest_records
from .resolve import resolve
from .synthesize import synthesize
from .export import build_state, export_entities, recompute

__all__ = [
    "Store",
    "collect",
    "import_decisions",
    "active_decisions",
    "latest_records",
    "resolve",
    "synthesize",
    "build_state",
    "export_entities",
    "recompute",
]
