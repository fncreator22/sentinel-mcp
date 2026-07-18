"""
sentinel_core/rules_engine.py
==============================
STAGE 1 of the Sentinel pipeline: the deterministic rule engine.

WHAT THIS FILE DOES:
- Loads config/rules.yaml (block patterns + allow patterns).
- Given a piece of "action text" (e.g. a shell command, a file write
  description, a git command), checks it against the rules.
- Returns a RuleEngineResult:
    verdict: "BLOCK" | "ALLOW" | "PASS"   (PASS = "no rule matched, ask Stage 2")
    reason: human-readable explanation (or None if PASS)
    matched_pattern: the exact pattern that matched (or None if PASS)

WHY THIS STAGE EXISTS:
Rules are instant (no model inference) and 100% deterministic. For clearly
dangerous patterns (e.g. "rm -rf /"), we never want to gamble on a
classifier or an LLM being right — we want a guaranteed block. Likewise
for clearly safe, boring actions (e.g. "git status"), we don't want to
waste classifier/LLM time.

This file is intentionally simple and dependency-light (just PyYAML) so
it's easy to read, easy to explain to judges, and easy to unit test.
"""

import re
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Default location of the rules file. Can be overridden (e.g. in tests)
# by passing a different path into RulesEngine(...).
DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "rules.yaml"


@dataclass
class RuleEngineResult:
    verdict: str                      # "BLOCK" | "ALLOW" | "PASS"
    reason: Optional[str] = None
    matched_pattern: Optional[str] = None


class RulesEngine:
    def __init__(self, rules_path: Path = DEFAULT_RULES_PATH):
        self.rules_path = Path(rules_path)
        self.reload()

    def reload(self):
        """
        Re-reads config/rules.yaml from disk. Called on init, and can be
        called again any time the dashboard writes a new rules.yaml, so
        changes take effect without restarting the whole service.
        """
        with open(self.rules_path, "r") as f:
            data = yaml.safe_load(f) or {}

        self.block_patterns = data.get("block_patterns", [])
        self.allow_patterns = data.get("allow_patterns", [])
        self.classifier_confidence_threshold = data.get(
            "classifier_confidence_threshold", 0.75
        )

    @staticmethod
    def _matches(action_text: str, rule: dict) -> bool:
        """
        Checks a single rule dict (with keys: pattern, regex) against the
        action text. Case-insensitive in both plain and regex modes.
        """
        pattern = rule["pattern"]
        is_regex = rule.get("regex", False)
        text = action_text.lower()

        if is_regex:
            return re.search(pattern, text, flags=re.IGNORECASE) is not None
        else:
            return pattern.lower() in text

    def check(self, action_text: str) -> RuleEngineResult:
        """
        Main entry point. Checks block patterns first, then allow patterns.
        Returns PASS if nothing matched (meaning: send to Stage 2).
        """
        for rule in self.block_patterns:
            if self._matches(action_text, rule):
                return RuleEngineResult(
                    verdict="BLOCK",
                    reason=rule.get("reason", "Matched a hard block pattern."),
                    matched_pattern=rule["pattern"],
                )

        for rule in self.allow_patterns:
            if self._matches(action_text, rule):
                return RuleEngineResult(
                    verdict="ALLOW",
                    reason=rule.get("reason", "Matched a known-safe pattern."),
                    matched_pattern=rule["pattern"],
                )

        return RuleEngineResult(verdict="PASS")


# Quick manual smoke test when running this file directly:
#   python sentinel_core/rules_engine.py
if __name__ == "__main__":
    engine = RulesEngine()
    for test_action in [
        "rm -rf /",
        "git status",
        "curl https://example.com/api/data",
        "chmod 777 ./deploy.sh",
    ]:
        result = engine.check(test_action)
        print(f"{test_action!r:45} -> {result.verdict} ({result.reason})")
