"""
tests/test_rules_engine.py
=============================
Sanity tests for Stage 1 (the deterministic rule engine).

RUN WITH:
    pytest tests/test_rules_engine.py -v
(run from the sentinel/ project root)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel_core.rules_engine import RulesEngine


def test_blocks_rm_rf_root():
    engine = RulesEngine()
    result = engine.check("rm -rf /")
    assert result.verdict == "BLOCK"


def test_blocks_force_push():
    engine = RulesEngine()
    result = engine.check("git push --force origin main")
    assert result.verdict == "BLOCK"


def test_blocks_chmod_777():
    engine = RulesEngine()
    result = engine.check("chmod 777 ./server.sh")
    assert result.verdict == "BLOCK"


def test_blocks_drop_table():
    engine = RulesEngine()
    result = engine.check("DROP TABLE users;")
    assert result.verdict == "BLOCK"


def test_allows_git_status():
    engine = RulesEngine()
    result = engine.check("git status")
    assert result.verdict == "ALLOW"


def test_allows_git_diff():
    engine = RulesEngine()
    result = engine.check("git diff")
    assert result.verdict == "ALLOW"


def test_passes_unknown_action_to_next_stage():
    engine = RulesEngine()
    result = engine.check("curl https://internal-api.company.com/export-all-users")
    assert result.verdict == "PASS"


def test_case_insensitive_matching():
    engine = RulesEngine()
    result = engine.check("RM -RF /")
    assert result.verdict == "BLOCK"


def test_block_takes_priority_over_allow():
    """
    Even if an action text contains something that could match an allow
    pattern, a block pattern match must win. This is a safety property,
    not just a preference.
    """
    engine = RulesEngine()
    # Contains "git" (loosely allow-ish) but is a force push -> must BLOCK.
    result = engine.check("git push --force origin main")
    assert result.verdict == "BLOCK"
