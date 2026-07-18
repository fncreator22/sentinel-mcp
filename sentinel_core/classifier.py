"""
sentinel_core/classifier.py
=============================
STAGE 2 of the Sentinel pipeline: the trained risk classifier.

WHAT THIS FILE DOES:
- Loads the TF-IDF vectorizer + Logistic Regression model that were trained
  by train/train_classifier.py and saved into
  sentinel_core/model_artifacts/{model.pkl, vectorizer.pkl}.
- Given an action text, predicts "safe" or "risky" plus a confidence score.
- If confidence is BELOW the configured threshold, tells the orchestrator
  to escalate to Stage 3 (the local LLM reviewer) instead of trusting the
  classifier's own verdict.

WHY CONFIDENCE MATTERS:
A simple TF-IDF + LogisticRegression model is fast and explainable, but it
can be unsure about genuinely novel or ambiguous phrasing it never saw in
training. Rather than silently guessing, we treat low confidence as a
signal to hand off to the (slower but more reasoning-capable) local LLM
in Stage 3. This is what keeps the "train our own model" claim honest:
the model does real work on the common cases, and we're transparent about
where it's not confident enough to be the final word.
"""

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ARTIFACTS_DIR = Path(__file__).resolve().parent / "model_artifacts"
MODEL_PATH = ARTIFACTS_DIR / "model.pkl"
VECTORIZER_PATH = ARTIFACTS_DIR / "vectorizer.pkl"


@dataclass
class ClassifierResult:
    label: str              # "safe" | "risky"
    confidence: float       # 0.0 - 1.0, probability of the predicted label
    needs_escalation: bool  # True if confidence is below threshold


class RiskClassifier:
    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        vectorizer_path: Path = VECTORIZER_PATH,
        confidence_threshold: float = 0.75,
    ):
        self.confidence_threshold = confidence_threshold

        if not model_path.exists() or not vectorizer_path.exists():
            raise FileNotFoundError(
                "Trained model not found. Run `python train/train_classifier.py` "
                f"first. Expected files at:\n  {model_path}\n  {vectorizer_path}"
            )

        with open(model_path, "rb") as f:
            self.model = pickle.load(f)
        with open(vectorizer_path, "rb") as f:
            self.vectorizer = pickle.load(f)

    def predict(self, action_text: str) -> ClassifierResult:
        vec = self.vectorizer.transform([action_text])
        probs = self.model.predict_proba(vec)[0]
        classes = list(self.model.classes_)

        best_idx = probs.argmax()
        label = classes[best_idx]
        confidence = float(probs[best_idx])

        return ClassifierResult(
            label=label,
            confidence=confidence,
            needs_escalation=confidence < self.confidence_threshold,
        )


if __name__ == "__main__":
    clf = RiskClassifier()
    for action in [
        "git status",
        "rm -rf /tmp/build",
        "curl https://internal-api.company.com/export-all-users",
        "npm install express",
    ]:
        result = clf.predict(action)
        print(f"{action!r:55} -> {result.label} "
              f"(confidence={result.confidence:.2f}, "
              f"escalate={result.needs_escalation})")
