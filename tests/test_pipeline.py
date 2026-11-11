"""采集 → 归并 → 导出 → 证据链的端到端与回归检查。

覆盖：同名异厂保持分开、无厂商转载停留候选区、别名撤销后恢复、
重复决定幂等、导入顺序无关、互斥冲突整体待处理、撤稿只撤对应证据、
同级同修订并列值、字段位置可回溯原始页面。
"""
import itertools
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from equipment_identity import cli
from equipment_identity.collector import collect
from equipment_identity.decisions import import_decisions
from equipment_identity.export import build_state, export_entities, recompute
from equipment_identity.records import latest_records
from equipment_identity.normalize import (
    normalize_model,
    normalize_text,
    normalize_vendor_code,
)
from equipment_identity.resolve import resolve
from equipment_identity.store import Store
from equipment_identity.synthesize import synthesize
from source_site.__main__ import running

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = json.loads((ROOT / "sources" / "decisions.json").read_text(encoding="utf-8"))
D1, D2, D3 = DECISIONS  # d1 same(a,c)；d2 different(a,b)；d3 revoke(d1)
NOW = "2026-11-11T00:00:00+00:00"

A, B, C, D = ("local:maker:a", "local:maker:b", "local:brief:c", "local:reprint:d")


def entity_of(state, key):
    for ent in state["entities"]:
        if key in ent["members"]:
            return ent
    return None


def canonical(state):
    """用于跨顺序比较的规范化视图（不含时间戳等易变项）。"""
    return json.dumps({
        "entities": sorted((e["entity_id"], tuple(e["members"])) for e in state["entities"]),
        "candidates": state["candidates"],
        "conflicts": state["conflicts"],
        "unresolved": state["unresolved"],
        "retracted": state["retracted"],
        "dangling": sorted((d["decision_id"], d["reason"]) for d in state["dangling_decisions"]),
    }, ensure_ascii=False, sort_keys=True)


def fake_record(key, kind, revision, code, model, title, params=(), obs="obs-x"):
    """构造与 latest_records 输出同形的记录视图，用于纯函数单元测试。"""
    source, kind_part, rid = key.split(":")
    fields = []
    if title is not None:
        fields.append({"name": "title", "value": title, "position": None})
    if code is not None:
        fields.append({"name": "maker_code", "value": code, "position": None})
    if model is not None:
        fields.append({"name": "model", "value": model, "position": None})
    for name, value in params:
        fields.append({"name": name, "value": value, "position": None})
    return {
        "key": key, "source": source, "kind": kind_part, "record_id": rid,
        "ref": "%s:%s" % (kind_part, rid), "active": True, "status": 200,
        "obs_id": obs, "version": 1, "fetched_at": NOW, "revision": revision,
        "fields": fields, "title": title, "maker_name": None,
        "maker_code": code, "model": model,
        "title_norm": normalize_text(title or ""),
        "model_norm": normalize_model(model or ""),
        "maker_code_norm": normalize_vendor_code(code),
        "history": [],
    }


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eid-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = Store(Path(self.tmp) / "data")

    def collect_once(self):
        with running() as (base, _source):
            return collect(self.store, base)

    # ---- 采集与离线重算 ----

    def test_collect_then_offline_recompute(self):
        summary = self.collect_once()
        self.assertEqual(4, len(summary["pages"]))
        # 来源已关闭：重算与查询只读本地原始记录
        state = recompute(self.store)
        keys = {r["key"] for r in state["records"]}
        self.assertEqual({A, B, C, D}, keys)
        self.assertTrue(all(r["active"] for r in state["records"]))

    def test_observations_are_append_only_versions(self):
        with running() as (base, _source):
            collect(self.store, base)
            collect(self.store, base)
        self.assertEqual(8, len(self.store.observations()))
        records = latest_records(self.store.observations())
        self.assertTrue(all(len(r["history"]) == 2 for r in records))
        self.assertTrue(all(r["version"] == 2 for r in records))
        raw_files = list((self.store.root / "raw").iterdir())
        self.assertEqual(8, len(raw_files))

    def test_field_positions_trace_to_raw_page(self):
        self.collect_once()
        state = build_state(self.store)
        rec_a = next(r for r in state["records"] if r["key"] == A)
        obs = next(o for o in self.store.observations()
                   if o["obs_id"] == rec_a["obs_id"])
        body = self.store.read_body(obs).decode("utf-8")
        field = next(f for f in rec_a["fields"] if f["name"] == "载荷")
        pos = field["position"]
        self.assertEqual("<dd>20千克</dd>", body[pos["start"]:pos["end"]])
        self.assertEqual("article>dl>dd[0]", pos["selector"])
        # 全角型号原样保留在原始记录中
        model = next(f for f in rec_a["fields"] if f["name"] == "model")
        self.assertEqual("Ｒ－１０", model["value"])
        self.assertEqual("R-10", rec_a["model_norm"])

    # ---- 实体归并规则 ----

    def test_same_name_different_vendor_stay_separate(self):
        self.collect_once()
        state = build_state(self.store)
        ent_a, ent_b = entity_of(state, A), entity_of(state, B)
        self.assertIsNotNone(ent_a)
        self.assertIsNotNone(ent_b)
        self.assertNotEqual(ent_a["entity_id"], ent_b["entity_id"])
        # 名称与型号相同也只是候选，绝不自动合并
        pair = [c for c in state["candidates"] if {c["left"], c["right"]} == {A, B}]
        self.assertEqual(1, len(pair))
        self.assertEqual("open", pair[0]["status"])
        self.assertIn("model-exact", pair[0]["signals"])

    def test_vendorless_reprint_stays_in_candidate_zone(self):
        self.collect_once()
        state = build_state(self.store)
        self.assertIsNone(entity_of(state, D))
        self.assertIn(D, state["unresolved"])
        partners = [
            c for c in state["candidates"] if D in (c["left"], c["right"])
        ]
        self.assertTrue(partners)
        self.assertTrue(all(c["status"] == "open" for c in partners))

    def test_alias_then_revoke_restores_evidence_based_state(self):
        self.collect_once()
        export_entities(self.store, trigger="collect", now=NOW)
        # 加入可靠别名：a 与 c 合并，厂商页参数优先
        import_decisions(self.store, [D1], now=NOW)
        state = export_entities(self.store, trigger="import-decisions", now=NOW)
        ent = entity_of(state, A)
        self.assertEqual(sorted([A, C]), sorted(ent["members"]))
        load = ent["fields"]["载荷"]
        self.assertEqual(["20千克"], [v["value"] for v in load["values"]])
        self.assertEqual(["18千克"], [s["value"] for s in load["suppressed"]])
        self.assertEqual(A, load["values"][0]["sources"][0]["record"])
        # 撤销别名：字段与实体关系恢复到有依据的状态
        import_decisions(self.store, [D3], now=NOW)
        state = export_entities(self.store, trigger="import-decisions", now=NOW)
        ent_a, ent_c = entity_of(state, A), entity_of(state, C)
        self.assertNotEqual(ent_a["entity_id"], ent_c["entity_id"])
        self.assertEqual(["20千克"],
                         [v["value"] for v in ent_a["fields"]["载荷"]["values"]])
        self.assertEqual(["18千克"],
                         [v["value"] for v in ent_c["fields"]["载荷"]["values"]])
        # 沿革可查询：先归并、后拆分
        events = self.store.lineage()
        types = [e["type"] for e in events]
        self.assertIn("merge", types)
        self.assertIn("split", types)
        merged = next(e for e in events if e["type"] == "merge")
        split = next(e for e in events if e["type"] == "split")
        self.assertEqual(merged["to"], split["from"])

    def test_explicit_different_blocks_candidate(self):
        self.collect_once()
        import_decisions(self.store, DECISIONS, now=NOW)
        state = build_state(self.store)
        pair = [c for c in state["candidates"] if {c["left"], c["right"]} == {A, B}]
        self.assertEqual(1, len(pair))
        self.assertEqual("blocked", pair[0]["status"])
        self.assertNotEqual(entity_of(state, A)["entity_id"],
                            entity_of(state, B)["entity_id"])

    # ---- 决定导入 ----

    def test_duplicate_decisions_are_idempotent(self):
        self.collect_once()
        first = import_decisions(self.store, DECISIONS, now=NOW)
        self.assertEqual(["d1", "d2", "d3"], first["applied"])
        before = canonical(build_state(self.store))
        second = import_decisions(self.store, DECISIONS, now=NOW)
        self.assertEqual([], second["applied"])
        self.assertEqual(["d1", "d2", "d3"], second["skipped_duplicate"])
        self.assertEqual(before, canonical(build_state(self.store)))
        self.assertEqual(3, len(self.store.decisions()))

    def test_conflicting_decision_id_rejected(self):
        self.collect_once()
        import_decisions(self.store, [D1], now=NOW)
        report = import_decisions(self.store, [{
            "decision_id": "d1", "operation": "same",
            "left": "maker:a", "right": "maker:b",
        }], now=NOW)
        self.assertEqual([], report["applied"])
        self.assertEqual(1, len(report["errors"]))
        state = build_state(self.store)
        self.assertEqual(sorted([A, C]), sorted(entity_of(state, A)["members"]))

    def test_import_order_does_not_change_result(self):
        self.collect_once()
        template = Path(self.tmp) / "template"
        shutil.copytree(self.store.root, template)
        results = set()
        for i, perm in enumerate(itertools.permutations(DECISIONS)):
            target = Path(self.tmp) / ("perm-%d" % i)
            shutil.copytree(template, target)
            store = Store(target)
            report = import_decisions(store, list(perm), now=NOW)
            self.assertEqual(3, len(report["applied"]), report)
            self.assertEqual([], report["errors"], report)
            results.add(canonical(build_state(store)))
        self.assertEqual(1, len(results))

    # ---- 互斥冲突 ----

    def test_mutual_exclusion_conflict_is_order_independent(self):
        self.collect_once()
        template = Path(self.tmp) / "template"
        shutil.copytree(self.store.root, template)
        conflict_decisions = [
            {"decision_id": "s1", "operation": "same",
             "left": "maker:a", "right": "brief:c"},
            {"decision_id": "s2", "operation": "same",
             "left": "maker:b", "right": "brief:c"},
            {"decision_id": "x1", "operation": "different",
             "left": "maker:a", "right": "maker:b"},
        ]
        results = set()
        for i, perm in enumerate(itertools.permutations(conflict_decisions)):
            target = Path(self.tmp) / ("conf-%d" % i)
            shutil.copytree(template, target)
            store = Store(target)
            import_decisions(store, list(perm), now=NOW)
            state = build_state(store)
            # 甲=乙=丙的合并链与甲≠丙冲突：整体待处理，不产生实体
            self.assertIsNone(entity_of(state, A))
            self.assertIsNone(entity_of(state, B))
            self.assertIsNone(entity_of(state, C))
            self.assertEqual(1, len(state["conflicts"]))
            conflict = state["conflicts"][0]
            self.assertEqual(sorted([A, B, C]), conflict["members"])
            self.assertEqual(["x1"], [v["decision_id"] for v in conflict["violated"]])
            self.assertIn("x1", conflict["explanation"])
            self.assertIn("s1", conflict["explanation"])
            self.assertIn("s2", conflict["explanation"])
            results.add(canonical(state))
        self.assertEqual(1, len(results))

    # ---- 撤稿 ----

    def test_retraction_removes_only_its_evidence(self):
        with running() as (base, source):
            collect(self.store, base)
            export_entities(self.store, trigger="collect", now=NOW)
            import_decisions(self.store, [D1], now=NOW)
            merged = export_entities(self.store, trigger="import-decisions", now=NOW)
            merged_id = entity_of(merged, A)["entity_id"]
            # 来源撤下简报 c
            source.phase = "withdrawn"
            collect(self.store, base)
            state = export_entities(self.store, trigger="collect", now=NOW)
            rec_c = next(r for r in state["records"] if r["key"] == C)
            self.assertFalse(rec_c["active"])
            self.assertIn(C, state["retracted"])
            # 撤稿只撤掉对应证据：实体退回 a 自身，c 的参数不再参与
            ent_a = entity_of(state, A)
            self.assertEqual([A], ent_a["members"])
            self.assertEqual(["20千克"],
                             [v["value"] for v in ent_a["fields"]["载荷"]["values"]])
            self.assertEqual(["d1"], [d["decision_id"] for d in state["dangling_decisions"]])
            # 原始观察仍全部保留
            self.assertEqual(8, len(self.store.observations()))
            # 来源恢复后，同一别名决定再次生效
            source.phase = "base"
            collect(self.store, base)
            state = export_entities(self.store, trigger="collect", now=NOW)
            ent = entity_of(state, A)
            self.assertEqual(sorted([A, C]), sorted(ent["members"]))
            self.assertEqual(merged_id, ent["entity_id"])
        events = self.store.lineage()
        self.assertTrue(any(merged_id in e["from"] for e in events))
        self.assertTrue(any(merged_id in e["to"] for e in events))


class ResolveUnitTests(unittest.TestCase):
    """纯函数层面的归并与合成规则。"""

    def test_exact_key_merges_without_human_decision(self):
        r1 = fake_record("local:maker:x", "maker", 1, "FA", "R-10", "甲",
                         params=[("载荷", "18千克")], obs="o1")
        r2 = fake_record("local:maker:y", "maker", 2, "FA", "Ｒ－１０", "甲",
                         params=[("载荷", "20千克")], obs="o2")
        result = resolve([r1, r2], [])
        self.assertEqual(1, len(result["entities"]))
        self.assertEqual(sorted([r1["key"], r2["key"]]),
                         result["entities"][0]["members"])
        fields = synthesize([r1, r2])
        # 同级取较新修订
        self.assertEqual(["20千克"], [v["value"] for v in fields["载荷"]["values"]])
        self.assertEqual(["18千克"], [s["value"] for s in fields["载荷"]["suppressed"]])

    def test_same_level_same_revision_keeps_parallel_values(self):
        def build(order):
            r1 = fake_record("local:maker:x", "maker", 2, "FA", "R-10", "甲",
                             params=[("载荷", "20千克")], obs="o1")
            r2 = fake_record("local:maker:y", "maker", 2, "FA", "R-10", "甲",
                             params=[("载荷", "25千克")], obs="o2")
            records = [r1, r2] if order == 0 else [r2, r1]
            return synthesize(records)["载荷"]

        first, second = build(0), build(1)
        self.assertEqual("contested", first["status"])
        self.assertEqual(["20千克", "25千克"],
                         [v["value"] for v in first["values"]])
        # 并列值不按采集先后覆盖：两种输入顺序结果一致
        self.assertEqual(first, second)

    def test_key_merge_cannot_bypass_explicit_different(self):
        r1 = fake_record("local:maker:x", "maker", 2, "FA", "R-10", "甲")
        r2 = fake_record("local:maker:y", "maker", 2, "FA", "R-10", "甲")
        decisions = [{
            "decision_id": "x1", "operation": "different",
            "left": "maker:x", "right": "maker:y",
        }]
        result = resolve([r1, r2], decisions)
        self.assertEqual([], result["entities"])
        self.assertEqual(1, len(result["conflicts"]))
        self.assertEqual(["x1"],
                         [v["decision_id"] for v in result["conflicts"][0]["violated"]])

    def test_name_similarity_alone_never_merges(self):
        r1 = fake_record("local:maker:x", "maker", 2, "FA", "R-10", "巡查一号")
        r2 = fake_record("local:maker:y", "maker", 2, "FB", "R-10", "巡查一号")
        result = resolve([r1, r2], [])
        self.assertEqual(2, len(result["entities"]))
        self.assertEqual(1, len(result["candidates"]))
        self.assertEqual("open", result["candidates"][0]["status"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eid-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data = str(Path(self.tmp) / "data")

    def test_full_cli_flow(self):
        with running() as (base, _source):
            self.assertEqual(0, cli.main(
                ["--data", self.data, "collect", "--source", base]))
        decisions_file = str(ROOT / "sources" / "decisions.json")
        self.assertEqual(0, cli.main(
            ["--data", self.data, "import-decisions", decisions_file]))
        self.assertEqual(0, cli.main(["--data", self.data, "status"]))
        out = str(Path(self.tmp) / "export.json")
        self.assertEqual(0, cli.main(["--data", self.data, "export", "--out", out]))
        payload = json.loads(Path(out).read_text(encoding="utf-8"))
        # d1 已被 d3 撤销：三个厂商记录各自成实体，转载停留候选区
        self.assertEqual(3, len(payload["entities"]))
        self.assertEqual([D], payload["unresolved"])
        self.assertEqual([], payload["conflicts"])
        self.assertEqual(0, cli.main(["--data", self.data, "recompute"]))
        ent_id = payload["entities"][0]["entity_id"]
        self.assertEqual(0, cli.main(["--data", self.data, "evidence", ent_id]))
        self.assertEqual(0, cli.main(["--data", self.data, "evidence", "maker:a"]))
        self.assertEqual(0, cli.main(["--data", self.data, "lineage", ent_id]))


if __name__ == "__main__":
    unittest.main()
