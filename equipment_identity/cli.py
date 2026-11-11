"""命令行入口：采集 → 导入决定 → 导出实体 → 查询证据链 → 离线重算。"""
from __future__ import annotations

import argparse
import json
import sys

from .collector import collect
from .decisions import import_decisions
from .export import build_state, export_entities, recompute, utcnow
from .store import Store


def _store(args) -> Store:
    return Store(args.data)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_collect(args) -> int:
    store = _store(args)
    summary = collect(store, args.source, source_name=args.source_name)
    export_entities(store, trigger="collect")
    for page in summary["pages"]:
        print("采集 %(url)s → %(status)s (%(obs_id)s, 版本 %(version)s, 记录 %(record_ref)s)" % page)
    for error in summary["errors"]:
        print("采集失败 %(url)s: %(error)s" % error, file=sys.stderr)
    return 0 if not summary["errors"] else 1


def cmd_import_decisions(args) -> int:
    store = _store(args)
    with open(args.file, encoding="utf-8") as fh:
        decisions = json.load(fh)
    report = import_decisions(store, decisions, now=utcnow())
    export_entities(store, trigger="import-decisions")
    print("已应用: %s" % ("、".join(report["applied"]) or "无"))
    if report["skipped_duplicate"]:
        print("重复跳过: %s" % "、".join(report["skipped_duplicate"]))
    for error in report["errors"]:
        print("拒绝 %(decision_id)s: %(error)s" % error, file=sys.stderr)
    return 0 if not report["errors"] else 1


def _print_state(state) -> None:
    print("生成时间: %s" % state["generated_at"])
    print("\n[记录]")
    for r in state["records"]:
        flag = "有效" if r["active"] else "已撤稿"
        print("  %s (%s, rev%s, %s, 观察 %s 版本%s)"
              % (r["key"], r["kind"], r["revision"], flag, r["obs_id"], r["version"]))
    print("\n[实体]")
    for ent in state["entities"]:
        print("  %s 成员: %s" % (ent["entity_id"], "、".join(ent["members"])))
    if state["unresolved"]:
        print("\n[候选区记录（身份不足，未成为实体）]")
        for key in state["unresolved"]:
            print("  %s" % key)
    if state["candidates"]:
        print("\n[相似候选（仅相似，未合并）]")
        for c in state["candidates"]:
            print("  %s ~ %s 得分 %.2f 信号 %s [%s]"
                  % (c["left"], c["right"], c["score"],
                     "/".join(c["signals"]), c["status"]))
    if state["conflicts"]:
        print("\n[冲突待处理]")
        for conflict in state["conflicts"]:
            print("  %s" % conflict["explanation"])
    if state["dangling_decisions"]:
        print("\n[悬空决定（引用缺失或已撤稿）]")
        for d in state["dangling_decisions"]:
            print("  %s %s: %s" % (d["decision_id"], d["operation"], d["reason"]))


def cmd_status(args) -> int:
    state = build_state(_store(args))
    if args.json:
        _print_json(state)
    else:
        _print_state(state)
    return 0


def cmd_export(args) -> int:
    store = _store(args)
    state = export_entities(store, trigger="export")
    payload = {
        "generated_at": state["generated_at"],
        "entities": state["entities"],
        "candidates": state["candidates"],
        "conflicts": state["conflicts"],
        "unresolved": state["unresolved"],
        "retracted": state["retracted"],
        "dangling_decisions": state["dangling_decisions"],
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        print("已导出 %d 个实体 → %s" % (len(state["entities"]), args.out))
    else:
        _print_json(payload)
    return 0


def _print_entity_evidence(state, entity_id) -> int:
    for ent in state["entities"]:
        if ent["entity_id"] != entity_id:
            continue
        print("实体 %s  成员: %s" % (entity_id, "、".join(ent["members"])))
        for edge in ent["evidence"]:
            if edge["type"] == "key":
                print("  键证据: 厂商 %s / 型号 %s ← %s"
                      % (edge["vendor_code"], edge["model"], "、".join(edge["members"])))
            else:
                print("  人工决定 %s: %s = %s"
                      % (edge["decision_id"], edge["left"], edge["right"]))
        print("  [字段]")
        for name, field in sorted(ent["fields"].items()):
            for value in field["values"]:
                print("  %s = %s  [%s]" % (name, value["value"], field["rule"]))
                for src in value["sources"]:
                    print("    出处: %(record)s (%(observation)s, %(kind)s rev%(revision)s, "
                          "%(selector)s @%(start)s-%(end)s)" % src)
            for sup in field["suppressed"]:
                for src in sup["sources"]:
                    print("    被覆盖: %s ← %s (%s, %s)"
                          % (sup["value"], src["record"], src["observation"], sup["reason"]))
        return 0
    print("未找到实体 %s" % entity_id, file=sys.stderr)
    return 1


def _print_record_evidence(state, store, ref) -> int:
    matches = [r for r in state["records"] if r["key"] == ref or r["ref"] == ref]
    if not matches:
        print("未找到记录 %s" % ref, file=sys.stderr)
        return 1
    record = matches[0]
    print("记录 %s (%s, %s)" % (record["key"], record["kind"],
                                "有效" if record["active"] else "已撤稿"))
    print("  观察历史:")
    for h in record["history"]:
        print("    %(obs_id)s 版本%(version)s 状态%(status)s %(fetched_at)s" % h)
    print("  字段（位置相对原始页面）:")
    for f in record["fields"]:
        pos = f.get("position") or {}
        print("    %s = %s  [%s @%s-%s, 观察 %s]"
              % (f["name"], f["value"], pos.get("selector"),
                 pos.get("start"), pos.get("end"), record["obs_id"]))
    for d in state["decisions"]:
        refs = {d.get("left"), d.get("right")}
        if record["ref"] in refs or record["key"] in refs:
            print("  相关决定: %s %s %s ↔ %s [%s]"
                  % (d["decision_id"], d["operation"], d.get("left"),
                     d.get("right"), d["status"]))
    for ent in state["entities"]:
        if record["key"] in ent["members"]:
            print("  所属实体: %s" % ent["entity_id"])
    for c in state["candidates"]:
        if record["key"] in (c["left"], c["right"]):
            print("  候选: %s ~ %s [%s]" % (c["left"], c["right"], c["status"]))
    if record["key"] in state["unresolved"]:
        print("  状态: 候选区（身份不足，未成为实体）")
    if not record["active"] and record.get("last_known"):
        known = record["last_known"]
        print("  撤稿前最后可见: 标题=%s 型号=%s 厂商=%s (观察 %s)"
              % (known["title"], known["model"], known["maker"], known["obs_id"]))
    return 0


def cmd_evidence(args) -> int:
    store = _store(args)
    state = build_state(store)
    if args.ref.startswith("E-"):
        return _print_entity_evidence(state, args.ref)
    return _print_record_evidence(state, store, args.ref)


def cmd_lineage(args) -> int:
    store = _store(args)
    events = store.lineage()
    if args.entity_id:
        events = [e for e in events
                  if args.entity_id in e["from"] or args.entity_id in e["to"]]
    if not events:
        print("无沿革事件")
        return 0
    for e in events:
        print("#%(seq)s %(at)s [%(trigger)s] %(type)s: %(from)s → %(to)s" % {
            "seq": e["seq"], "at": e["at"], "trigger": e["trigger"],
            "type": e["type"],
            "from": "、".join(e["from"]) or "（无）",
            "to": "、".join(e["to"]) or "（无）",
        })
    return 0


def cmd_recompute(args) -> int:
    state = recompute(_store(args))
    print("已从原始记录重算（未访问网络）：实体 %d，候选 %d，冲突 %d，撤稿 %d"
          % (len(state["entities"]), len(state["candidates"]),
             len(state["conflicts"]), len(state["retracted"])))
    if state["lineage_events"]:
        print("沿革事件 %d 条" % len(state["lineage_events"]))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="equipment_identity",
        description="装备资料采集、实体归并与证据链查询")
    parser.add_argument("--data", default="eid_data", help="数据目录（默认 eid_data）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="从来源站采集一次（只追加观察）")
    p.add_argument("--source", required=True, help="来源站地址，如 http://127.0.0.1:8090")
    p.add_argument("--source-name", default="local", help="来源标识（默认 local）")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("import-decisions", help="导入人工决定（JSON 文件）")
    p.add_argument("file", help="决定文件，如 sources/decisions.json")
    p.set_defaults(func=cmd_import_decisions)

    p = sub.add_parser("status", help="查看当前记录、实体、候选与冲突")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("export", help="导出实体（含候选、冲突、撤稿）")
    p.add_argument("--out", help="导出文件路径，缺省打印到标准输出")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("evidence", help="查询实体或记录的证据链")
    p.add_argument("ref", help="实体标识 E-xxx 或记录引用 maker:a")
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("lineage", help="查询实体拆分/归并沿革")
    p.add_argument("entity_id", nargs="?", help="实体标识，缺省列出全部沿革")
    p.set_defaults(func=cmd_lineage)

    p = sub.add_parser("recompute", help="仅从原始记录重算，不访问网络")
    p.set_defaults(func=cmd_recompute)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
