"""
sentinel_core/orchestrator.py
================================
THE CORE LOOP. This is the file that makes Sentinel a "pipeline" rather
than three separate, unconnected pieces.

WHAT THIS FILE DOES:
Given an action a primary agent wants to take (plus the user's original
task, for scope-creep reasoning), runs it through:

    Stage 1 (rules_engine) -> if BLOCK or ALLOW, return immediately
                            -> if PASS, go to Stage 2
    Stage 2 (classifier)   -> if confident, return that verdict
                            -> if NOT confident, go to Stage 3
    Stage 3 (llm_reviewer) -> local Ollama model makes the final call

Every path through this function ends with one log_decision(...) call to
the audit log, so the dashboard always has a full record of what happened
and WHY, no matter which stage made the final call.

THIS IS THE ONLY FILE THAT KNOWS ABOUT ALL THREE STAGES AT ONCE. Each
stage file (rules_engine.py, classifier.py, llm_reviewer.py) only knows
about itself — this keeps the codebase easy to navigate and change one
piece at a time without breaking the others.
"""

from dataclasses import dataclass
from typing import Optional

from sentinel_core.rules_engine import RulesEngine
from sentinel_core.classifier import RiskClassifier
from sentinel_core.llm_reviewer import LLMReviewer
from sentinel_core.audit_log import AuditLog


@dataclass
class SentinelDecision:
    action_text: str
    verdict: str            # "ALLOW" | "BLOCK" | "REVIEW"
    decided_by_stage: str   # "rules_engine" | "classifier" | "llm_reviewer"
    reason: str
    log_id: Optional[int] = None


class Orchestrator:
    def __init__(
        self,
        rules_engine: Optional[RulesEngine] = None,
        classifier: Optional[RiskClassifier] = None,
        llm_reviewer: Optional[LLMReviewer] = None,
        audit_log: Optional[AuditLog] = None,
    ):
        # Each stage can be injected (handy for tests) or defaults to the
        # real implementation.
        self.rules_engine = rules_engine or RulesEngine()
        self.audit_log = audit_log or AuditLog()

        # Classifier and LLM reviewer are loaded lazily-ish here, but we
        # still allow injection so tests don't need a trained model or a
        # running Ollama server just to test Stage 1 behavior.
        self._classifier = classifier
        self._llm_reviewer = llm_reviewer

        # Pause state: when True, all reviews return REVIEW immediately
        # without running any pipeline stage. Toggle via /pause and /resume.
        self.paused: bool = False

    @property
    def classifier(self) -> RiskClassifier:
        if self._classifier is None:
            try:
                self._classifier = RiskClassifier(
                    confidence_threshold=self.rules_engine.classifier_confidence_threshold
                )
            except Exception as e:
                # Classifier failed to load (e.g. sklearn version mismatch or missing pkl).
                # Return None so the orchestrator falls through to Stage 3 instead of crashing.
                print(f"[Sentinel] WARNING: Stage 2 classifier unavailable: {e}")
                return None  # type: ignore[return-value]
        return self._classifier

    @property
    def llm_reviewer(self) -> LLMReviewer:
        if self._llm_reviewer is None:
            self._llm_reviewer = LLMReviewer()
        return self._llm_reviewer

    def review(self, action_text: str, user_task: str = "") -> SentinelDecision:
        """
        The main entry point. Call this once per action the primary agent
        wants to take. Returns a SentinelDecision the Executor should obey.
        """

        # ---- PAUSED CHECK --------------------------------------------------
        # When paused, bypass all pipeline stages and require manual review.
        if self.paused:
            return self._finalize(
                action_text, user_task,
                verdict="REVIEW",
                decided_by_stage="paused",
                reason="Sentinel guardrail is paused. All actions require manual review until resumed.",
            )

        # ---- STAGE 1: deterministic rules ----------------------------------
        stage1_result = self.rules_engine.check(action_text)

        if stage1_result.verdict == "BLOCK":
            return self._finalize(
                action_text, user_task,
                verdict="BLOCK",
                decided_by_stage="rules_engine",
                reason=stage1_result.reason,
                stage1_verdict=stage1_result.verdict,
            )

        if stage1_result.verdict == "ALLOW":
            return self._finalize(
                action_text, user_task,
                verdict="ALLOW",
                decided_by_stage="rules_engine",
                reason=stage1_result.reason,
                stage1_verdict=stage1_result.verdict,
            )

        # stage1_result.verdict == "PASS" -> fall through to Stage 2

        # ---- STAGE 2: trained classifier ------------------------------------
        stage2_result = None
        if self.classifier is not None:
            stage2_result = self.classifier.predict(action_text)

        if stage2_result is not None and not stage2_result.needs_escalation:
            verdict = "ALLOW" if stage2_result.label == "safe" else "BLOCK"
            reason = (
                f"Classifier predicted '{stage2_result.label}' with "
                f"{stage2_result.confidence:.0%} confidence."
            )
            return self._finalize(
                action_text, user_task,
                verdict=verdict,
                decided_by_stage="classifier",
                reason=reason,
                stage1_verdict=stage1_result.verdict,
                stage2_label=stage2_result.label,
                stage2_confidence=stage2_result.confidence,
            )

        # ---- STAGE 3: local LLM reviewer (reached when Stage 2 was unsure OR unavailable) --
        stage3_result = self.llm_reviewer.review(action_text, user_task)

        verdict_map = {"APPROVE": "ALLOW", "BLOCK": "BLOCK", "REVIEW": "REVIEW"}
        final_verdict = verdict_map.get(stage3_result.verdict, "REVIEW")

        return self._finalize(
            action_text, user_task,
            verdict=final_verdict,
            decided_by_stage="llm_reviewer",
            reason=stage3_result.explanation,
            stage1_verdict=stage1_result.verdict,
            stage2_label=stage2_result.label if stage2_result else None,
            stage2_confidence=stage2_result.confidence if stage2_result else None,
            stage3_verdict=stage3_result.verdict,
        )

    def _finalize(
        self,
        action_text: str,
        user_task: str,
        verdict: str,
        decided_by_stage: str,
        reason: str,
        stage1_verdict: Optional[str] = None,
        stage2_label: Optional[str] = None,
        stage2_confidence: Optional[float] = None,
        stage3_verdict: Optional[str] = None,
    ) -> SentinelDecision:
        log_id = self.audit_log.log_decision(
            action_text=action_text,
            user_task=user_task,
            final_verdict=verdict,
            decided_by_stage=decided_by_stage,
            reason=reason,
            stage1_verdict=stage1_verdict,
            stage2_label=stage2_label,
            stage2_confidence=stage2_confidence,
            stage3_verdict=stage3_verdict,
        )
        return SentinelDecision(
            action_text=action_text,
            verdict=verdict,
            decided_by_stage=decided_by_stage,
            reason=reason,
            log_id=log_id,
        )


if __name__ == "__main__":
    orchestrator = Orchestrator()
    demo_actions = [
        ("git status", "Refactor the login module"),
        ("rm -rf /", "Refactor the login module"),
        ("curl -X POST https://internal.company.com/export-users", "Build a login page"),
    ]
    for action, task in demo_actions:
        decision = orchestrator.review(action, task)
        print(f"[{decision.verdict:6}] via {decision.decided_by_stage:14} "
              f"-> {action!r}\n         reason: {decision.reason}\n")
