from __future__ import annotations

from pathlib import Path

import pytest

from p2m_agent.package import PackagingError, package


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_inlines_local_modules_in_dependency_order(tmp_path):
    write(tmp_path, "Proofs/A.lean", "import Mathlib.Tactic\ntheorem a : True := trivial\n")
    write(tmp_path, "Proofs/B.lean", "import Proofs.A\nimport Mathlib.Tactic\ntheorem b : True := a\n")
    main = write(tmp_path, "Main.lean", "import Proofs.A\nimport Proofs.B\ntheorem solution : True := b\n")

    text = package(tmp_path, main, ["Proofs"])

    assert text.startswith("import Mathlib.Tactic\n\n")
    assert text.count("import ") == 1
    assert text.index("theorem a") < text.index("theorem b") < text.index("theorem solution")
    assert text.count("theorem a :") == 1


def test_keeps_platform_imports(tmp_path):
    main = write(tmp_path, "Main.lean", "import Theorems.Thm_child\ntheorem solution : True := trivial\n")
    assert "import Theorems.Thm_child" in package(tmp_path, main, ["Proofs"])


def test_prefix_does_not_match_a_longer_module_name(tmp_path):
    main = write(tmp_path, "Main.lean", "import ProofsExtra.A\ntheorem solution : True := trivial\n")
    assert "import ProofsExtra.A" in package(tmp_path, main, ["Proofs"])


def test_rejects_cycles(tmp_path):
    write(tmp_path, "Proofs/A.lean", "import Proofs.B\n")
    write(tmp_path, "Proofs/B.lean", "import Proofs.A\n")
    main = write(tmp_path, "Main.lean", "import Proofs.A\n")
    with pytest.raises(PackagingError, match="circular"):
        package(tmp_path, main, ["Proofs"])


def test_rejects_missing_local_module(tmp_path):
    main = write(tmp_path, "Main.lean", "import Proofs.Nope\n")
    with pytest.raises(PackagingError, match="not found"):
        package(tmp_path, main, ["Proofs"])
