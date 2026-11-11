"""人工决定的导入与生效集合计算。

决定带 decision_id，三种操作：same（确认相同）、different（明确不同）、
revoke（撤销先前确认）。规则：

- 同一个 decision_id 重复导入不会执行两次（幂等）；
- 编号相同但内容不同的导入被拒绝，不覆盖既有决定；
- 撤销目标尚未导入时，撤销作为「待生效撤销」接受：目标决定一旦出现
  即被撤销。生效集合由完整决定日志声明式计算，因此最终状态只取决于
  决定集合本身，与导入顺序无关；
- 撤销只针对确认类决定（same/different），不能撤销另一条撤销。
"""
from __future__ import annotations

OPERATIONS = ("same", "different", "revoke")

_PAYLOAD_KEYS = ("operation", "left", "right", "target")


def _payload(item: dict) -> dict:
    return {key: item.get(key) for key in _PAYLOAD_KEYS}


def import_decisions(store, decisions: list[dict], now: str) -> dict:
    """导入一批决定，返回 applied / pending / skipped_duplicate / errors。"""
    existing = {d["decision_id"]: d for d in store.decisions()}
    report = {"applied": [], "pending": [], "skipped_duplicate": [], "errors": []}
    for item in decisions:
        did = item.get("decision_id")
        payload = _payload(item)
        if not did:
            report["errors"].append({"decision_id": None, "error": "缺少 decision_id"})
            continue
        if did in existing:
            if _payload(existing[did]) == payload:
                # 同一个决定重复导入：不重复执行。
                report["skipped_duplicate"].append(did)
            else:
                report["errors"].append({
                    "decision_id": did,
                    "error": "决定编号已存在且内容不同，拒绝覆盖",
                })
            continue
        error = _validate(payload, existing)
        if error:
            report["errors"].append({"decision_id": did, "error": error})
            continue
        is_pending = (payload["operation"] == "revoke"
                      and payload["target"] not in existing)
        record = {"decision_id": did, "imported_at": now, **payload}
        store.append_decision(record)
        existing[did] = record
        report["applied"].append(did)
        if is_pending:
            report["pending"].append(did)
    return report


def _validate(payload: dict, existing: dict) -> str | None:
    op = payload["operation"]
    if op not in OPERATIONS:
        return "未知操作: %r" % (op,)
    if op in ("same", "different"):
        if not payload["left"] or not payload["right"]:
            return "缺少 left/right 引用"
        if payload["left"] == payload["right"]:
            return "left 与 right 相同"
        return None
    # revoke：目标缺失时接受为待生效撤销；目标为撤销决定时拒绝。
    target = payload["target"]
    if not target:
        return "缺少 target"
    if target in existing and existing[target]["operation"] == "revoke":
        return "不能撤销另一条撤销决定"
    return None


def active_decisions(decisions: list[dict]) -> tuple[list[dict], set[str]]:
    """生效集合 = 全部 same/different 减去被撤销者。

    撤销决定本身不可再被撤销（导入时已校验），因此生效集合是整个
    决定日志的纯函数，与导入顺序无关。
    """
    revoked = {d["target"] for d in decisions if d["operation"] == "revoke"}
    active = [
        d for d in decisions
        if d["operation"] in ("same", "different") and d["decision_id"] not in revoked
    ]
    return active, revoked


def pending_revokes(decisions: list[dict]) -> list[dict]:
    """目标尚未出现的撤销（目标决定一旦导入即被撤销）。"""
    known = {d["decision_id"] for d in decisions}
    return [
        d for d in decisions
        if d["operation"] == "revoke" and d["target"] not in known
    ]
