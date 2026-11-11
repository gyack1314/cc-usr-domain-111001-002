"""资料页 HTML 解析，并为每个字段保留其在原始页面中的位置。

位置用「选择器 + 起止偏移」表示，偏移是相对原始 HTML 字节的字符区间，
资料员可以据此把每项参数回溯到原文的确切位置。
"""
from __future__ import annotations

from html.parser import HTMLParser


class _PageParser(HTMLParser):
    """记录每个元素的范围与文本的极简解析器。"""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.text = text
        self._line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self._line_starts.append(i + 1)
        self._stack: list[list] = []
        self.elements: list[dict] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        self._stack.append([tag, dict(attrs), self._offset(), []])

    def handle_data(self, data):
        for frame in self._stack:
            frame[3].append(data)

    def handle_endtag(self, tag):
        end_start = self._offset()
        closing = "</" + tag + ">"
        if self.text[end_start:end_start + len(closing)].lower() != closing:
            found = self.text.lower().find(closing, end_start)
            if found != -1:
                end_start = found
        end = end_start + len(closing)
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                frame = self._stack.pop(i)
                self.elements.append({
                    "tag": tag,
                    "attrs": frame[1],
                    "start": frame[2],
                    "end": end,
                    "text": "".join(frame[3]),
                })
                return


def _position(element: dict, selector: str) -> dict:
    return {"selector": selector, "start": element["start"], "end": element["end"]}


def parse_page(text: str) -> dict:
    """解析单个资料页，返回记录字段及其位置。

    页面结构约定见 sources/contract.json：article 携带 data-record-id、
    data-kind、data-revision；h1 为标题；span.maker 携带厂商名与
    data-code 厂商编码；span.model 为型号；dl 内 dt/dd 为参数名值对。
    """
    parser = _PageParser(text)
    parser.feed(text)
    parser.close()
    articles = [e for e in parser.elements if e["tag"] == "article"]
    if not articles:
        raise ValueError("页面缺少 article 记录元素")
    article = articles[0]
    attrs = article["attrs"]
    record_id = attrs.get("data-record-id")
    kind = attrs.get("data-kind")
    if not record_id or not kind:
        raise ValueError("article 缺少 data-record-id 或 data-kind")
    try:
        revision = int(attrs.get("data-revision") or 0)
    except ValueError:
        revision = 0

    inner = [
        e for e in parser.elements
        if e is not article
        and article["start"] <= e["start"]
        and e["end"] <= article["end"]
    ]

    def first(tag, class_name=None):
        for el in inner:
            if el["tag"] != tag:
                continue
            if class_name and class_name not in (el["attrs"].get("class") or "").split():
                continue
            return el
        return None

    title_el = first("h1")
    maker_el = first("span", "maker")
    model_el = first("span", "model")

    title = None
    if title_el is not None:
        title = {"value": title_el["text"].strip(),
                 "position": _position(title_el, "article>h1")}

    maker = None
    if maker_el is not None:
        maker = {
            "name": maker_el["text"].strip(),
            "code": maker_el["attrs"].get("data-code"),
            "position": _position(maker_el, "article>span.maker"),
        }

    model = None
    if model_el is not None:
        model = {"value": model_el["text"].strip(),
                 "position": _position(model_el, "article>span.model")}

    params = []
    pending_dt = None
    for el in sorted(inner, key=lambda e: e["start"]):
        if el["tag"] == "dt":
            pending_dt = el
        elif el["tag"] == "dd" and pending_dt is not None:
            params.append({
                "name": pending_dt["text"].strip(),
                "value": el["text"].strip(),
                "name_position": _position(pending_dt, "article>dl>dt[%d]" % len(params)),
                "value_position": _position(el, "article>dl>dd[%d]" % len(params)),
            })
            pending_dt = None

    return {
        "record_id": record_id,
        "kind": kind,
        "revision": revision,
        "title": title,
        "maker": maker,
        "model": model,
        "params": params,
    }
