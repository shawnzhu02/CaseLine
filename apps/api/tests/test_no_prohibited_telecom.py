"""Guava is the only telephony/SMS integration (spec §2, §15, §28)."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROHIBITED = re.compile(r"twilio|telnyx|amazon[\s_-]?connect|vonage|plivo|bandwidth\.com", re.IGNORECASE)
SKIP_DIRS = {".git", ".venv", "node_modules", ".next", "__pycache__", ".pytest_cache", ".ruff_cache", "docs"}
# Documentation may *mention* prohibited vendors (to prohibit them); code and config may not.
SKIP_FILES = {"CASELINE_IMPLEMENTATION_PLAN.md", "README.md", "test_no_prohibited_telecom.py", "uv.lock",
              "ci.yml"}  # ci.yml holds the grep pattern that enforces this rule
CHECK_SUFFIXES = {".py", ".toml", ".txt", ".yaml", ".yml", ".json", ".example", ".cfg", ".ini", ".ps1", ".ts",
                  ".tsx", ".mjs"}


def test_no_prohibited_telecom_dependencies_or_code():
    hits = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.name in SKIP_FILES or SKIP_DIRS & set(path.relative_to(REPO).parts):
            continue
        if path.suffix not in CHECK_SUFFIXES and path.name != ".env.example":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{path.relative_to(REPO)}: {m.group(0)}" for m in PROHIBITED.finditer(text)]
    assert hits == []
