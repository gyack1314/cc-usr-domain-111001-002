"""从原始观察组装每条记录的最新视图。

每个记录键为「来源:栏目:站内编号」。同一记录的多次观察形成观察版本
序列，最新一次 200 观察决定记录当前内容；最新观察非 200（如撤稿 410）
时记录标记为撤稿，其字段不再作为证据，但历史观察全部保留。
"""
from __future__ import annotations

from .normalize import normalize_model, normalize_text, normalize_vendor_code


def record_key(source: str, kind: str, record_id: str) -> str:
    return "%s:%s:%s" % (source, kind, record_id)


def split_ref(ref: str) -> tuple[str, str]:
    kind, _, record_id = ref.partition(":")
    return kind, record_id


def _fields_from_parsed(parsed: dict) -> list[dict]:
    fields = []
    if parsed.get("title"):
        fields.append({
            "name": "title",
            "value": parsed["title"]["value"],
            "position": parsed["title"]["position"],
        })
    maker = parsed.get("maker")
    if maker:
        fields.append({
            "name": "maker_name",
            "value": maker["name"],
            "position": maker["position"],
        })
        if maker.get("code"):
            fields.append({
                "name": "maker_code",
                "value": maker["code"],
                "position": {
                    "selector": "article>span.maker@data-code",
                    "start": maker["position"]["start"],
                    "end": maker["position"]["end"],
                },
            })
    if parsed.get("model"):
        fields.append({
            "name": "model",
            "value": parsed["model"]["value"],
            "position": parsed["model"]["position"],
        })
    for param in parsed.get("params", []):
        fields.append({
            "name": normalize_text(param["name"]),
            "name_raw": param["name"],
            "value": param["value"],
            "position": param["value_position"],
            "name_position": param["name_position"],
        })
    return fields


def _build_record(source: str, ref: str, obs: dict, history: list[dict],
                  last_active: dict | None) -> dict:
    kind, rid = split_ref(ref)
    parsed = obs.get("parsed")
    active = obs.get("status") == 200 and parsed is not None
    fields = _fields_from_parsed(parsed) if active else []

    def field_value(name):
        for f in fields:
            if f["name"] == name:
                return f["value"]
        return None

    record = {
        "key": record_key(source, kind, rid),
        "source": source,
        "kind": kind,
        "record_id": rid,
        "ref": ref,
        "active": active,
        "status": obs.get("status"),
        "obs_id": obs["obs_id"],
        "version": obs["version"],
        "fetched_at": obs["fetched_at"],
        "revision": (parsed or {}).get("revision", 0) if active else 0,
        "fields": fields,
        "history": [
            {"obs_id": h["obs_id"], "version": h["version"],
             "status": h["status"], "fetched_at": h["fetched_at"]}
            for h in history
        ],
    }
    record["title"] = field_value("title")
    record["maker_name"] = field_value("maker_name")
    record["maker_code"] = field_value("maker_code")
    record["model"] = field_value("model")
    record["title_norm"] = normalize_text(record["title"] or "")
    record["model_norm"] = normalize_model(record["model"] or "")
    record["maker_code_norm"] = normalize_vendor_code(record["maker_code"])
    if not active and last_active and last_active.get("parsed"):
        # 撤稿记录保留最后可见内容供查阅，但不作为证据。
        record["last_known"] = {
            "obs_id": last_active["obs_id"],
            "title": (last_active["parsed"].get("title") or {}).get("value"),
            "model": (last_active["parsed"].get("model") or {}).get("value"),
            "maker": (last_active["parsed"].get("maker") or {}).get("name"),
        }
    return record


def latest_records(observations: list[dict]) -> list[dict]:
    """每个记录取最新观察版本，返回记录视图列表（含撤稿标记）。"""
    by_ref: dict[tuple[str, str], list[dict]] = {}
    for obs in observations:
        ref = obs.get("record_ref")
        if not ref:
            continue
        by_ref.setdefault((obs["source"], ref), []).append(obs)

    records = []
    for (source, ref), history in sorted(by_ref.items()):
        history = sorted(history, key=lambda o: (o["version"], o["obs_id"]))
        latest = history[-1]
        last_active = None
        for obs in reversed(history):
            if obs.get("status") == 200 and obs.get("parsed"):
                last_active = obs
                break
        records.append(_build_record(source, ref, latest, history, last_active))
    return records
