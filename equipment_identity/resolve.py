"""实体解析：键证据与人工决定 → 实体 / 候选 / 冲突。

合并证据只有两类：
1. 完全一致的非空厂商编码 + 规范化型号（键证据）；
2. 生效的人工 same 决定。
名称相似只产生候选，绝不直接合并。

明确不同（different）是不可绕过的约束：若一个组件内同时存在合并证据
与互斥决定，整个组件标记为冲突待处理并解释证据链，绝不按导入顺序
偷偷选一边。解析是「记录 + 生效决定」的纯函数，结果与顺序无关。
"""
from __future__ import annotations

import hashlib

from .normalize import model_similarity

#: 型号相似度达到该阈值才作为候选信号。
SIMILARITY_THRESHOLD = 0.6


def entity_id_for(members: list[str]) -> str:
    """实体标识由成员集合内容寻址：成员不变则标识不变。"""
    digest = hashlib.sha1(",".join(sorted(members)).encode("utf-8")).hexdigest()
    return "E-" + digest[:12]


class _UnionFind:
    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, key):
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # 确定性：始终以字典序较小者为根。
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra


class _RefIndex:
    """把决定中的引用（kind:id 或完整键）解析为记录键。"""

    def __init__(self, records):
        self.by_key = {r["key"]: r for r in records}
        self.by_short: dict[str, list[str]] = {}
        for r in records:
            self.by_short.setdefault(r["ref"], []).append(r["key"])

    def resolve(self, ref):
        """返回 (key, error)。key 为 None 时 error 说明原因。"""
        if ref in self.by_key:
            return ref, None
        if ref.count(":") == 1:
            matches = self.by_short.get(ref, [])
            if len(matches) == 1:
                return matches[0], None
            if len(matches) > 1:
                return None, "引用不唯一: %s 匹配 %s" % (ref, sorted(matches))
        return None, "记录不存在: %s" % ref


def _self_identifying(record: dict) -> bool:
    """记录能独立成为实体的条件：非空厂商编码 + 非空规范化型号。"""
    return bool(record["maker_code_norm"]) and bool(record["model_norm"])


def _signals(ra: dict, rb: dict) -> tuple[list[str], float]:
    """候选信号与打分；只用于候选，不参与合并。"""
    signals, score = [], 0.0
    if ra["model_norm"] and ra["model_norm"] == rb["model_norm"]:
        signals.append("model-exact")
        score = max(score, 0.9)
    else:
        sim = model_similarity(ra["model_norm"], rb["model_norm"])
        if sim >= SIMILARITY_THRESHOLD:
            signals.append("model-similar")
            score = max(score, round(sim, 3))
    if ra["title_norm"] and ra["title_norm"] == rb["title_norm"]:
        signals.append("title-exact")
        score = max(score, 0.5)
    if ra["maker_code_norm"] and ra["maker_code_norm"] == rb["maker_code_norm"]:
        signals.append("same-vendor")
        score = max(score, 0.4)
    return signals, score


def resolve(records: list[dict], decisions: list[dict]) -> dict:
    """解析实体。records 为记录视图（含撤稿标记），decisions 为生效决定。"""
    active_records = [r for r in records if r["active"]]
    by_key = {r["key"]: r for r in active_records}
    active_index = _RefIndex(active_records)
    full_index = _RefIndex(records)
    uf = _UnionFind(by_key)
    evidence = []
    dangling = []

    def resolve_active(ref):
        key, _ = active_index.resolve(ref)
        if key is not None:
            return key, None
        _, reason = full_index.resolve(ref)
        if reason is None:
            return None, "记录已撤稿: %s" % ref
        return None, reason

    # 1) 键证据：非空厂商编码 + 规范化型号完全一致。
    key_groups: dict[tuple[str, str], list[str]] = {}
    for r in active_records:
        if _self_identifying(r):
            key_groups.setdefault((r["maker_code_norm"], r["model_norm"]), []).append(r["key"])
    for (code, model), keys in sorted(key_groups.items()):
        if len(keys) > 1:
            keys = sorted(keys)
            evidence.append({
                "type": "key",
                "vendor_code": code,
                "model": model,
                "members": keys,
            })
            for other in keys[1:]:
                uf.union(keys[0], other)

    # 2) 人工 same 决定。
    same_decisions = sorted(
        (d for d in decisions if d["operation"] == "same"),
        key=lambda d: d["decision_id"],
    )
    for d in same_decisions:
        left, err_l = resolve_active(d["left"])
        right, err_r = resolve_active(d["right"])
        if left is None or right is None:
            dangling.append({**d, "reason": err_l or err_r})
            continue
        uf.union(left, right)
        evidence.append({
            "type": "decision",
            "decision_id": d["decision_id"],
            "left": left,
            "right": right,
        })

    components: dict[str, list[str]] = {}
    for key in by_key:
        components.setdefault(uf.find(key), []).append(key)

    # 3) different 约束：不可绕过的互斥条件。
    constraints = []
    violated: dict[str, list[dict]] = {}
    different_decisions = sorted(
        (d for d in decisions if d["operation"] == "different"),
        key=lambda d: d["decision_id"],
    )
    for d in different_decisions:
        left, err_l = resolve_active(d["left"])
        right, err_r = resolve_active(d["right"])
        if left is None or right is None:
            dangling.append({**d, "reason": err_l or err_r})
            continue
        if uf.find(left) == uf.find(right):
            constraints.append({**d, "left": left, "right": right, "status": "violated"})
            violated.setdefault(uf.find(left), []).append(d)
        else:
            constraints.append({**d, "left": left, "right": right, "status": "satisfied"})

    # 4) 组件分类：冲突整体待处理；孤立且无身份的记录留在候选区。
    entities, conflicts, unresolved = [], [], []
    for root in sorted(components):
        members = sorted(components[root])
        member_set = set(members)
        if root in violated:
            ev = [
                e for e in evidence
                if (e["type"] == "key" and member_set & set(e["members"]))
                or (e["type"] == "decision" and (e["left"] in member_set or e["right"] in member_set))
            ]
            bad = violated[root]
            conflicts.append({
                "members": members,
                "evidence": ev,
                "violated": [
                    {"decision_id": d["decision_id"], "left": d["left"], "right": d["right"]}
                    for d in bad
                ],
                "explanation": _explain_conflict(members, ev, bad),
            })
        elif len(members) == 1 and not _self_identifying(by_key[members[0]]):
            unresolved.append(members[0])
        else:
            entities.append({
                "entity_id": entity_id_for(members),
                "members": members,
                "evidence": [
                    e for e in evidence
                    if (e["type"] == "key" and set(e["members"]) <= member_set)
                    or (e["type"] == "decision" and e["left"] in member_set and e["right"] in member_set)
                ],
            })

    # 5) 候选：仅名称/厂商相似，绝不自动合并。
    blocked_pairs = {
        frozenset((c["left"], c["right"])) for c in constraints
    }
    candidates = []
    keys = sorted(by_key)
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            if uf.find(ka) == uf.find(kb):
                continue
            signals, score = _signals(by_key[ka], by_key[kb])
            if not signals:
                continue
            candidates.append({
                "left": ka,
                "right": kb,
                "score": score,
                "signals": signals,
                "status": "blocked" if frozenset((ka, kb)) in blocked_pairs else "open",
            })
    candidates.sort(key=lambda c: (c["left"], c["right"]))

    entities.sort(key=lambda e: e["entity_id"])
    conflicts.sort(key=lambda c: c["members"])
    return {
        "entities": entities,
        "conflicts": conflicts,
        "unresolved": sorted(unresolved),
        "candidates": candidates,
        "constraints": constraints,
        "dangling": sorted(dangling, key=lambda d: d["decision_id"]),
    }


def _explain_conflict(members, evidence, violated) -> str:
    parts = ["组件 %s 同时存在合并证据与互斥决定：" % "、".join(members)]
    for e in evidence:
        if e["type"] == "key":
            parts.append("键证据（厂商 %s / 型号 %s）连接 %s"
                         % (e["vendor_code"], e["model"], "、".join(e["members"])))
        else:
            parts.append("决定 %s 确认 %s 与 %s 相同"
                         % (e["decision_id"], e["left"], e["right"]))
    for d in violated:
        parts.append("但决定 %s 明确 %s 与 %s 不同"
                     % (d["decision_id"], d["left"], d["right"]))
    parts.append("整体待处理，不按导入顺序取舍。")
    return "；".join(parts)
