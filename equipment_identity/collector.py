"""HTTP 采集：每次抓取生成新的观察版本，只追加、不覆盖。

撤稿（如 410）也会留下观察记录，并沿用该地址此前已知的记录引用，
使重算时能把撤稿对应到具体记录、只撤掉对应证据。
"""
from __future__ import annotations

import hashlib
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .export import utcnow
from .parsing import parse_page


def _fetch(url: str, timeout: float = 10.0) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "equipment-identity/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as exc:  # 410/404 等：状态本身也是观察结果
        return exc.code, exc.read()


def collect(store, base_url: str, source_name: str = "local",
            now: str | None = None) -> dict:
    """采集来源站全部资料页，返回本次采集摘要。"""
    now = now or utcnow()
    base = base_url.rstrip("/")
    summary = {"source": source_name, "fetched_at": now,
               "pages": [], "errors": []}

    try:
        status, body = _fetch(base + "/index")
        index = json.loads(body.decode("utf-8")) if status == 200 else {}
    except (URLError, OSError, ValueError) as exc:
        summary["errors"].append({"url": base + "/index", "error": str(exc)})
        return summary

    previous: dict[str, list[dict]] = {}
    for obs in store.observations():
        if obs["source"] == source_name:
            previous.setdefault(obs["url"], []).append(obs)

    for path in index.get("pages", []):
        url = base + path
        try:
            status, body = _fetch(url)
        except (URLError, OSError) as exc:
            summary["errors"].append({"url": url, "error": str(exc)})
            continue
        history = previous.get(url, [])
        version = len(history) + 1
        parsed, record_ref, note = None, None, None
        if status == 200:
            try:
                parsed = parse_page(body.decode("utf-8"))
                record_ref = "%s:%s" % (parsed["kind"], parsed["record_id"])
            except (ValueError, UnicodeDecodeError) as exc:
                note = "解析失败: %s" % exc
        if record_ref is None and history:
            # 撤稿/异常页面沿用该地址此前已知的记录引用。
            record_ref = history[-1].get("record_ref")
        obs = {
            "obs_id": store.next_obs_id(),
            "source": source_name,
            "url": url,
            "path": path,
            "fetched_at": now,
            "status": status,
            "sha256": hashlib.sha256(body).hexdigest(),
            "version": version,
            "record_ref": record_ref,
            "parsed": parsed,
            "note": note,
        }
        store.append_observation(obs, body)
        summary["pages"].append({
            "url": url, "status": status, "obs_id": obs["obs_id"],
            "version": version, "record_ref": record_ref, "note": note,
        })
    return summary
