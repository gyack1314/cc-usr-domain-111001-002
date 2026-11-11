"""追加式存储：原始观察、人工决定、实体沿革。

原始观察、人工决定、沿革事件只追加、绝不覆盖；entities.json 是
派生快照，可以随时从原始数据重算，因此允许重写。
"""
from __future__ import annotations

import json
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    # ---- 原始观察（不可覆盖） ----

    def _obs_index(self) -> Path:
        return self.root / "observations.jsonl"

    def next_obs_id(self) -> str:
        return "obs-%04d" % (len(self.observations()) + 1)

    def append_observation(self, obs: dict, body: bytes | None) -> None:
        obs = dict(obs)
        if body is not None:
            obs["body_file"] = obs["obs_id"] + ".html"
        with self._obs_index().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obs, ensure_ascii=False, sort_keys=True) + "\n")
        if body is not None:
            # obs_id 全局唯一，写新文件而不是覆盖旧观察。
            (self.raw_dir / obs["body_file"]).write_bytes(body)

    def observations(self) -> list[dict]:
        path = self._obs_index()
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def read_body(self, obs: dict) -> bytes | None:
        name = obs.get("body_file")
        if not name:
            return None
        path = self.raw_dir / name
        return path.read_bytes() if path.exists() else None

    # ---- 人工决定（只追加，按 decision_id 去重） ----

    def _decisions_path(self) -> Path:
        return self.root / "decisions.jsonl"

    def append_decision(self, decision: dict) -> None:
        with self._decisions_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision, ensure_ascii=False, sort_keys=True) + "\n")

    def decisions(self) -> list[dict]:
        path = self._decisions_path()
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # ---- 实体沿革（只追加） ----

    def _lineage_path(self) -> Path:
        return self.root / "lineage.jsonl"

    def append_lineage(self, events: list[dict]) -> None:
        if not events:
            return
        with self._lineage_path().open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def lineage(self) -> list[dict]:
        path = self._lineage_path()
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # ---- 派生快照（可重算，允许重写） ----

    def _snapshot_path(self) -> Path:
        return self.root / "entities.json"

    def save_snapshot(self, snapshot: dict) -> None:
        self._snapshot_path().write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load_snapshot(self) -> dict | None:
        path = self._snapshot_path()
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
