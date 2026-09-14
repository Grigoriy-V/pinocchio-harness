"""`apply_patch`: the V4A patch over several files (roadmap 27 step 3).

The grammar Codex, Hermes and OpenClaw share; matched exactly, then with
whitespace forgiven; all or nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools import ToolError, patch_tools
from app.tools.patch import parse_patch, patch_paths


def tool(root: Path, **kw):
    return patch_tools(root, **kw)[0]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "import os\n\n\ndef main():\n    print('one')\n    return 1\n\n\ndef other():\n    print('one')\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_path / "old.txt").write_text("bye\n", encoding="utf-8")
    return tmp_path


def test_a_patch_updates_adds_and_deletes_in_one_call(project: Path) -> None:
    patch = """*** Begin Patch
*** Update File: app.py
@@ def other():
     print('one')
-    return 2
+    return 3
*** Add File: docs/new.md
+# New
+two lines
*** Delete File: old.txt
*** End Patch"""

    out = tool(project).run(patch=patch)

    assert "M app.py (+1 -1)" in out
    assert "A docs/new.md (2 lines)" in out
    assert "D old.txt" in out
    text = (project / "app.py").read_text(encoding="utf-8")
    assert "return 3" in text and "return 1" in text
    assert (project / "docs" / "new.md").read_text(encoding="utf-8") == "# New\ntwo lines"
    assert not (project / "old.txt").exists()


def test_the_anchor_line_chooses_between_identical_hunks(project: Path) -> None:
    patch = """*** Begin Patch
*** Update File: app.py
@@ def other():
-    print('one')
+    print('two')
*** End Patch"""

    tool(project).run(patch=patch)

    text = (project / "app.py").read_text(encoding="utf-8")
    assert text.count("print('one')") == 1 and text.index("print('two')") > text.index("def other")


def test_whitespace_at_the_ends_of_a_line_is_forgiven(project: Path) -> None:
    patch = """*** Begin Patch
*** Update File: app.py
 import os
+import sys
*** End Patch"""

    tool(project).run(patch=patch)

    assert (project / "app.py").read_text(encoding="utf-8").startswith("import os\nimport sys\n")


def test_a_hunk_that_does_not_match_touches_nothing(project: Path) -> None:
    before = (project / "app.py").read_text(encoding="utf-8")
    patch = """*** Begin Patch
*** Add File: made.txt
+x
*** Update File: app.py
-nothing like this
+something
*** End Patch"""

    with pytest.raises(ToolError, match="were not found") as caught:
        tool(project).run(patch=patch)

    assert "nothing like this" in str(caught.value)
    assert (project / "app.py").read_text(encoding="utf-8") == before
    assert not (project / "made.txt").exists(), "all or nothing"


def test_move_to_renames_and_end_of_file_anchors(project: Path) -> None:
    patch = """*** Begin Patch
*** Update File: app.py
*** Move to: pkg/app.py
@@ def other():
     return 2
+
+
+def last():
+    return 0
*** End of File
*** End Patch"""

    out = tool(project).run(patch=patch)

    assert "R app.py -> pkg/app.py" in out
    assert not (project / "app.py").exists()
    assert (project / "pkg" / "app.py").read_text(encoding="utf-8").rstrip().endswith("def last():\n    return 0")


def test_the_grammar_is_checked_and_a_fence_is_forgiven(project: Path) -> None:
    with pytest.raises(ToolError, match="starts with"):
        parse_patch("*** Update File: x\n-a\n+b\n*** End Patch")
    with pytest.raises(ToolError, match="ends with"):
        parse_patch("*** Begin Patch\n*** Update File: x\n-a\n+b")
    with pytest.raises(ToolError, match="no hunk follows"):
        parse_patch("*** Begin Patch\n*** Update File: x\n*** End Patch")
    fenced = "```\n*** Begin Patch\n*** Add File: a.txt\n+hi\n*** End Patch\n```"
    assert parse_patch(fenced)[0].lines == ["hi"]
    assert patch_paths("*** Begin Patch\n*** Update File: a\n*** Move to: b\n-x\n+y\n*** End Patch") == ["a", "b"]


def test_confined_patches_stay_in_the_root_and_open_ones_ask_outside(project: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    patch = f"*** Begin Patch\n*** Add File: {outside / 'x.txt'}\n+hi\n*** End Patch"

    with pytest.raises(ToolError, match="outside the allowed root"):
        tool(project).run(patch=patch)
    opened = tool(project, open_reads=True)
    assert opened.asks({"patch": patch}) is True
    assert opened.asks({"patch": "*** Begin Patch\n*** Add File: in.txt\n+hi\n*** End Patch"}) is False
    assert opened.mutates


def test_the_contract_shows_the_shape(project: Path) -> None:
    described = tool(project).schema()["function"]["description"]

    assert "*** Begin Patch" in described and "*** End Patch" in described
    assert "Returns:" in described and "Leaves:" in described
