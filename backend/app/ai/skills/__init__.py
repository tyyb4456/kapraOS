"""Skills package (Step 1: core skill only)."""

from pathlib import Path

CORE_SKILL_NAME = "kapraos-core"
CORE_SKILL_DIR = Path(__file__).resolve().parent / "kapraos-core"
CORE_SKILL_PATH = CORE_SKILL_DIR / "SKILL.md"


def list_skill_names() -> list[str]:
    """Skill names available in Step 1 (no agent zoo — just core)."""
    return [CORE_SKILL_NAME]


def load_core_skill() -> str:
    """Read the core ``SKILL.md`` content."""
    return CORE_SKILL_PATH.read_text(encoding="utf-8")
