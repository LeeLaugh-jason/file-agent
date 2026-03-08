"""计划导入校验测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from file_agent.cli import load_plan_json
from file_agent.scanner import FileInfo


def _fi(root: Path, rel_path: str) -> FileInfo:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    return FileInfo(path=p, rel_path=rel_path, root_dir=str(root), ext=p.suffix, size_bytes=1)


class TestLoadPlanJsonValidation:
    def test_rejects_invalid_key_and_value(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        files = [_fi(root, "a.txt")]

        plan_path = tmp_path / "plan.json"
        plan_path.write_text(
            json.dumps({"missing.txt": "分类A", "a.txt": "../evil"}, ensure_ascii=False),
            encoding="utf-8",
        )

        with patch("file_agent.cli.console") as mock_console:
            loaded = load_plan_json(str(plan_path), files)

        assert loaded is None
        assert mock_console.print.called

    def test_valid_plan_is_loaded(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        files = [_fi(root, "a.txt")]

        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps({"a.txt": "分类A"}, ensure_ascii=False), encoding="utf-8")

        loaded = load_plan_json(str(plan_path), files)
        assert loaded == {"a.txt": "分类A"}
