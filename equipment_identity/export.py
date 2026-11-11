"""状态组装、实体导出与沿革（拆分/归并）记录。

build_state 把「原始观察 + 决定日志」折叠成当前完整状态，全程不访问
网络；export_entities 在此基础上与上一快照比对，把实体的拆分、归并、
出现、消失写入只追加的沿革日志，使已导出的稳定实体标识始终可查询。
"""
from __future__ import annotations

from datetime import datetime, timezone

from .decisions import active_decisions, pending_revokes
from .records import latest_records
from .resolve import resolve
from .synthesize import synthesize


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_state(store, now: str | None = None) -> dict:
    """从存储重算完整状态；纯函数，不访问网络。"""
    now = now or utcnow()
    observations = store.observations()
    records = latest_records(observations)
    by_key = {r["key"]: r for r in records}
    decisions = store.decisions()
    active, revoked = active_decisions(decisions)
    resolution = resolve(records, active)

    entities = []
    for ent in resolution["entities"]:
        members = ent["members"]
        entities.append({
            **ent,
            "fields": synthesize([by_key[m] for m in members]),
        })

    dangling_ids = {d["decision_id"] for d in resolution["dangling"]}
    pending_ids = {d["decision_id"] for d in pending_revokes(decisions)}
    decisions_view = []
    for d in decisions:
        if d["operation"] == "revoke":
            status = "revoke-pending" if d["decision_id"] in pending_ids else "revoke"
        elif d["decision_id"] in revoked:
            status = "revoked"
        elif d["decision_id"] in dangling_ids:
            status = "dangling"
        else:
            status = "active"
        decisions_view.append({**d, "status": status})

    return {
        "generated_at": now,
        "records": records,
        "decisions": decisions_view,
        "entities": entities,
        "candidates": resolution["candidates"],
        "conflicts": resolution["conflicts"],
        "constraints": resolution["constraints"],
        "unresolved": resolution["unresolved"],
        "retracted": sorted(k for k, r in by_key.items() if not r["active"]),
        "dangling_decisions": resolution["dangling"],
        "pending_revokes": [d for d in decisions_view if d["decision_id"] in pending_ids],
    }


def _diff_lineage(old: dict[str, list[str]], new: dict[str, list[str]],
                  trigger: str, now: str, start_seq: int) -> list[dict]:
    """比对前后实体成员集合，产生拆分/归并/出现/消失/变更事件。"""
    events = []

    def emit(kind, from_ids, to_ids):
        events.append({
            "seq": start_seq + len(events),
            "at": now,
            "trigger": trigger,
            "type": kind,
            "from": sorted(from_ids),
            "to": sorted(to_ids),
        })

    # 新实体 ← 旧实体 的成员重叠关系。
    new_overlaps: dict[str, set[str]] = {eid: set() for eid in new}
    for old_id, old_members in old.items():
        old_set = set(old_members)
        for new_id, new_members in new.items():
            if old_set & set(new_members):
                new_overlaps[new_id].add(old_id)

    for old_id, old_members in sorted(old.items()):
        old_set = set(old_members)
        targets = [nid for nid, members in new.items() if old_set & set(members)]
        if not targets:
            emit("disappeared", [old_id], [])
        elif len(targets) > 1:
            emit("split", [old_id], targets)
        elif set(new[targets[0]]) != old_set and len(new_overlaps[targets[0]]) == 1:
            emit("updated", [old_id], targets)
    for new_id, sources in sorted(new_overlaps.items()):
        if not sources:
            emit("appeared", [], [new_id])
        elif len(sources) > 1:
            emit("merge", sources, [new_id])
    return events


def export_entities(store, trigger: str = "export", now: str | None = None) -> dict:
    """重算状态、更新沿革与快照，返回当前状态。不访问网络。"""
    now = now or utcnow()
    state = build_state(store, now=now)
    new_entities = {e["entity_id"]: e["members"] for e in state["entities"]}
    snapshot = store.load_snapshot() or {}
    old_entities = snapshot.get("entities", {})
    events = _diff_lineage(old_entities, new_entities, trigger, now,
                           start_seq=len(store.lineage()) + 1)
    store.append_lineage(events)
    store.save_snapshot({
        "generated_at": now,
        "trigger": trigger,
        "entities": new_entities,
    })
    state["lineage_events"] = events
    return state


def recompute(store, now: str | None = None) -> dict:
    """仅从原始记录与决定日志重算，不再次请求网页。"""
    return export_entities(store, trigger="recompute", now=now)
