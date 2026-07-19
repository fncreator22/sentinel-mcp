"""
sentinel_core/audit_log.py
=============================
Stores every review decision Sentinel makes in a local SQLite database.

WHAT THIS FILE DOES:
- Creates (if not present) a `decisions` table in sentinel.db
- Provides log_decision(...) to write a new row after each review
- Provides get_recent_decisions(...) to read rows back for the dashboard's
  live feed

WHY SQLITE:
Zero setup (it's a single file, ships with Python), which matters for a
hackathon demo — no separate database server to run. Good enough for the
audit trail volume this project produces.
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict

DB_PATH = Path(__file__).resolve().parent.parent / "sentinel.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action_text TEXT NOT NULL,
    user_task TEXT,
    final_verdict TEXT NOT NULL,
    decided_by_stage TEXT NOT NULL,
    reason TEXT,
    stage1_verdict TEXT,
    stage2_label TEXT,
    stage2_confidence REAL,
    stage3_verdict TEXT
);
"""


class AuditLog:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=30.0)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(CREATE_TABLE_SQL)
            conn.commit()

    def log_decision(
        self,
        action_text: str,
        final_verdict: str,
        decided_by_stage: str,
        reason: str = "",
        user_task: Optional[str] = None,
        stage1_verdict: Optional[str] = None,
        stage2_label: Optional[str] = None,
        stage2_confidence: Optional[float] = None,
        stage3_verdict: Optional[str] = None,
    ) -> int:
        """
        Writes one row to the decisions table. Returns the new row's id.
        decided_by_stage should be one of: "rules_engine", "classifier", "llm_reviewer"
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO decisions (
                    timestamp, action_text, user_task, final_verdict,
                    decided_by_stage, reason, stage1_verdict,
                    stage2_label, stage2_confidence, stage3_verdict
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp, action_text, user_task, final_verdict,
                    decided_by_stage, reason, stage1_verdict,
                    stage2_label, stage2_confidence, stage3_verdict,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_recent_decisions(self, limit: int = 50) -> List[Dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_summary_stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            stage1 = conn.execute("SELECT COUNT(*) FROM decisions WHERE decided_by_stage = 'rules_engine'").fetchone()[0]
            stage2 = conn.execute("SELECT COUNT(*) FROM decisions WHERE decided_by_stage = 'classifier'").fetchone()[0]
            stage3 = conn.execute("SELECT COUNT(*) FROM decisions WHERE decided_by_stage = 'llm_reviewer'").fetchone()[0]
            blocks = conn.execute("SELECT COUNT(*) FROM decisions WHERE final_verdict = 'BLOCK'").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            return {
                "stage1_detections": stage1,
                "stage2_detections": stage2,
                "stage3_detections": stage3,
                "recent_blocks": blocks,
                "total_decisions": total,
            }


if __name__ == "__main__":
    log = AuditLog()
    new_id = log.log_decision(
        action_text="git status",
        final_verdict="ALLOW",
        decided_by_stage="rules_engine",
        reason="Matched known-safe pattern.",
        stage1_verdict="ALLOW",
    )
    print(f"Logged decision id={new_id}")
    print(log.get_recent_decisions(5))
