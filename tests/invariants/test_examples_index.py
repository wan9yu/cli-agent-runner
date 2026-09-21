"""Each play directory under examples/ has a README and is listed in examples/README.md."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EX = REPO / "examples"


def _play_dirs() -> list[Path]:
    return sorted(
        p
        for p in EX.iterdir()
        if p.is_dir() and p.name != "__pycache__" and not p.name.startswith(".")
    )


def test_examples_readme_should_list_each_play_dir() -> None:
    text = (EX / "README.md").read_text(encoding="utf-8")
    assert "not" in text.lower() and "supervisor" in text.lower()
    assert "78/75/70" in text
    missing: list[str] = []
    for d in _play_dirs():
        if f"{d.name}/" not in text:
            missing.append(d.name)
        if not (d / "README.md").is_file():
            missing.append(f"{d.name}/README.md")
    assert not missing, f"examples/README.md missing play dirs or READMEs: {missing}"
