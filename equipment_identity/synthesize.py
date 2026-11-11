"""按字段合成实体参数。

规则（按字段独立执行）：
1. 来源级别优先：厂商页 maker > 活动简报 brief > 转载 reprint；
2. 同级时取来源声明的较新修订（data-revision 较大者）；
3. 同级同修订值冲突时保留并列值，绝不按采集先后覆盖。

每个保留值与被覆盖值都携带完整出处（记录、观察版本、字段位置），
资料员可以逐项回溯参数来源。
"""
from __future__ import annotations

#: 来源级别优先级，数值越大越可信。
KIND_PRIORITY = {"maker": 3, "brief": 2, "reprint": 1}


def _provenance(record: dict, field: dict) -> dict:
    position = field.get("position") or {}
    return {
        "record": record["key"],
        "observation": record["obs_id"],
        "kind": record["kind"],
        "revision": record["revision"],
        "selector": position.get("selector"),
        "start": position.get("start"),
        "end": position.get("end"),
    }


def synthesize(records: list[dict]) -> dict:
    """对一个实体的成员记录逐字段合成，返回 {字段名: 合成结果}。"""
    names = sorted({f["name"] for r in records for f in r["fields"]})
    result = {}
    for name in names:
        entries = [
            (r, f) for r in records for f in r["fields"] if f["name"] == name
        ]
        if not entries:
            continue
        best_priority = max(KIND_PRIORITY.get(r["kind"], 0) for r, _ in entries)
        at_level = [e for e in entries
                    if KIND_PRIORITY.get(e[0]["kind"], 0) == best_priority]
        best_revision = max(r["revision"] for r, _ in at_level)
        winners = [e for e in at_level if e[0]["revision"] == best_revision]

        values: dict[str, list[dict]] = {}
        for r, f in winners:
            values.setdefault(f["value"], []).append(_provenance(r, f))

        suppressed = []
        for r, f in entries:
            if any(r is wr and f is wf for wr, wf in winners):
                continue
            reason = ("较低来源级别" if KIND_PRIORITY.get(r["kind"], 0) < best_priority
                      else "较旧修订")
            suppressed.append({
                "value": f["value"],
                "sources": [_provenance(r, f)],
                "reason": reason,
            })

        rules = []
        if len(entries) == 1:
            rules.append("唯一来源")
        if len(at_level) < len(entries):
            rules.append("来源级别优先 maker>brief>reprint")
        if len(winners) < len(at_level):
            rules.append("同级取较新修订 rev%d" % best_revision)
        status = "single" if len(values) == 1 else "contested"
        if status == "contested":
            rules.append("同级同修订值冲突，并列保留")

        result[name] = {
            "status": status,
            "values": [
                {"value": value, "sources": sorted(sources, key=lambda s: s["record"])}
                for value, sources in sorted(values.items())
            ],
            "suppressed": sorted(suppressed, key=lambda s: (s["value"], s["reason"])),
            "rule": "；".join(rules),
        }
    return result
