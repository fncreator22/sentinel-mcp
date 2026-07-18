"""
train/train_classifier.py
==========================
Trains the Stage 2 risk classifier FROM SCRATCH on our own labeled data.

MODEL CHOICE: TF-IDF vectorizer + Logistic Regression (scikit-learn).
WHY THIS INSTEAD OF A "BIGGER" MODEL:
- It is genuinely trained by us on our own dataset (not a wrapped LLM call),
  which is the whole point of the "we trained our own model" claim.
- It is fast to train (seconds), fast at inference (milliseconds), and fully
  explainable — you can literally show judges the top weighted words for
  the "risky" class.
- It ships as two tiny files (model.pkl + vectorizer.pkl), which is exactly
  what the Hugging Face repo needs to be lightweight and README-first.

WHAT THIS SCRIPT DOES:
1. Loads data/training_examples.csv (columns: action_text, label)
2. Runs 5-fold stratified cross-validation and prints mean accuracy + std
3. Splits into a final train/test set and prints a full classification report
4. Fits a TfidfVectorizer on the training action text
5. Fits a LogisticRegression classifier on the TF-IDF features
6. Saves model.pkl + vectorizer.pkl into sentinel_core/model_artifacts/
   (used at runtime by classifier.py)
7. Prints the top 20 words/bigrams the model associates with RISKY
8. ALSO copies the same two files into the Hugging Face repo folder
   (../sentinel-risk-classifier/) so that repo stays in sync automatically

VECTORIZER IMPROVEMENTS (vs v1):
- ngram_range=(1, 3) — catches three-word danger signals like "curl pipe bash"
  or "delete namespace production", not just two-word pairs.
- sublinear_tf=True — applies log normalization to term frequencies,
  reducing the influence of very frequent common tokens and giving the model
  better signal from rarer but more diagnostic words.
- max_features=5000 — doubled from 2000, giving the model more vocabulary
  to work with now that the dataset is much larger.
- min_df=2 — ignore terms that appear in fewer than 2 documents, reducing
  noise from one-off typos or model hallucinations in the generated data.

USAGE:
    python train/train_classifier.py
"""

import csv
import pickle
import shutil
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.metrics import classification_report, accuracy_score
from sklearn.pipeline import Pipeline
import numpy as np

# ---- Paths ----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "training_examples.csv"
ARTIFACTS_DIR = PROJECT_ROOT / "sentinel_core" / "model_artifacts"
HF_REPO_DIR = PROJECT_ROOT.parent / "sentinel-risk-classifier"  # sibling repo

MODEL_FILENAME = "model.pkl"
VECTORIZER_FILENAME = "vectorizer.pkl"


def load_dataset(path: Path):
    texts, labels = [], []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            text = row["action_text"].strip()
            label = row["label"].strip()
            if text and label in ("safe", "risky"):
                texts.append(text)
                labels.append(label)
    return texts, labels


def main():
    print(f"Loading dataset from {DATA_PATH} ...")
    texts, labels = load_dataset(DATA_PATH)
    safe_count = labels.count("safe")
    risky_count = labels.count("risky")
    print(f"Loaded {len(texts)} examples ({safe_count} safe / {risky_count} risky)")
    print(f"Class ratio: safe={safe_count/len(texts):.1%}  risky={risky_count/len(texts):.1%}\n")

    # ---- Build the vectorizer ------------------------------------------------
    vectorizer = TfidfVectorizer(
        lowercase=True,
        ngram_range=(1, 3),       # unigrams, bigrams, trigrams
        sublinear_tf=True,        # log(1 + tf) — reduces dominance of frequent tokens
        min_df=2,                 # ignore terms appearing in fewer than 2 docs
        max_features=5000,        # larger vocabulary for bigger dataset
        strip_accents="unicode",
        analyzer="word",
        token_pattern=r"(?u)\b[\w/\.\-\$\:\@\>\<\|]+\b",  # keep path separators & special chars
    )

    clf = LogisticRegression(
        max_iter=2000,
        class_weight="balanced",   # compensates for any class imbalance
        C=5.0,                     # moderate regularization: generalizes well, not underfit
        solver="lbfgs",
        random_state=42,
    )

    # ---- 5-fold cross-validation before splitting ----------------------------
    print("Running 5-fold stratified cross-validation ...")
    pipeline = Pipeline([("tfidf", vectorizer), ("clf", clf)])
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(pipeline, texts, labels, cv=cv, scoring="accuracy")
    print(f"  CV accuracy: {cv_scores.mean():.3f} +/- {cv_scores.std():.3f}")
    cv_f1 = cross_val_score(pipeline, texts, labels, cv=cv, scoring="f1_macro")
    print(f"  CV macro-F1: {cv_f1.mean():.3f} +/- {cv_f1.std():.3f}\n")

    # ---- Hold-out test evaluation --------------------------------------------
    X_train, X_test, y_train, y_test = train_test_split(
        texts, labels, test_size=0.15, random_state=42, stratify=labels
    )

    X_train_vec = vectorizer.fit_transform(X_train)
    X_test_vec = vectorizer.transform(X_test)

    clf.fit(X_train_vec, y_train)

    y_pred = clf.predict(X_test_vec)
    acc = accuracy_score(y_test, y_pred)
    print(f"Hold-out test accuracy: {acc:.3f}\n")
    print(classification_report(y_test, y_pred))

    # ---- Confidence calibration check ----------------------------------------
    y_proba = clf.predict_proba(X_test_vec)
    confident = (y_proba.max(axis=1) >= 0.80).sum()
    total_test = len(y_test)
    print(f"Confident predictions (>= 80% confidence): {confident}/{total_test} = {confident/total_test:.1%}")
    print("(Sentinel's Stage 3 LLM only activates for predictions below the confidence threshold)\n")

    # ---- Retrain on full dataset before saving --------------------------------
    # Common practice: validate on held-out split, then train on all data
    # so the shipped model uses every labeled example available.
    X_full_vec = vectorizer.fit_transform(texts)
    clf.fit(X_full_vec, labels)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = ARTIFACTS_DIR / MODEL_FILENAME
    vectorizer_path = ARTIFACTS_DIR / VECTORIZER_FILENAME

    with open(model_path, "wb") as f:
        pickle.dump(clf, f)
    with open(vectorizer_path, "wb") as f:
        pickle.dump(vectorizer, f)

    print(f"Saved model      -> {model_path}")
    print(f"Saved vectorizer -> {vectorizer_path}")

    # ---- Explainability: top weighted words/trigrams for RISKY ---------------
    feature_names = vectorizer.get_feature_names_out()
    classes = list(clf.classes_)
    if len(classes) == 2:
        positive_class = classes[1]
        coefs = clf.coef_[0] if positive_class == "risky" else -clf.coef_[0]
    else:
        risky_index = classes.index("risky")
        coefs = clf.coef_[risky_index]

    top_risky_idx = coefs.argsort()[-20:][::-1]
    print("\nTop 20 words/phrases the model associates with RISKY:")
    for i in top_risky_idx:
        print(f"  {feature_names[i]:35s}  weight={coefs[i]:.3f}")

    top_safe_idx = coefs.argsort()[:10]
    print("\nTop 10 words/phrases the model associates with SAFE:")
    for i in top_safe_idx:
        print(f"  {feature_names[i]:35s}  weight={coefs[i]:.3f}")

    # ---- Sync to Hugging Face repo if it exists ------------------------------
    if HF_REPO_DIR.exists():
        shutil.copy(model_path, HF_REPO_DIR / MODEL_FILENAME)
        shutil.copy(vectorizer_path, HF_REPO_DIR / VECTORIZER_FILENAME)
        print(f"\nAlso synced model + vectorizer to {HF_REPO_DIR}")
    else:
        print(f"\n(Hugging Face repo folder not found at {HF_REPO_DIR} — skip sync)")


if __name__ == "__main__":
    main()
