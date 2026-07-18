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
2. Splits into train/test sets
3. Fits a TfidfVectorizer on the training action text
4. Fits a LogisticRegression classifier on the TF-IDF features
5. Prints accuracy + a classification report so you can show it in your demo
6. Saves model.pkl + vectorizer.pkl into sentinel_core/model_artifacts/
   (used at runtime by classifier.py)
7. ALSO copies the same two files into the Hugging Face repo folder
   (../sentinel-risk-classifier/) so that repo stays in sync automatically

USAGE:
    python train/train_classifier.py
"""

import csv
import pickle
import shutil
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

# ---- Paths ----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "training_examples.csv"
ARTIFACTS_DIR = PROJECT_ROOT / "sentinel_core" / "model_artifacts"
HF_REPO_DIR = PROJECT_ROOT.parent / "sentinel-risk-classifier"  # sibling repo

MODEL_FILENAME = "model.pkl"
VECTORIZER_FILENAME = "vectorizer.pkl"


def load_dataset(path: Path):
    texts, labels = [], []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            texts.append(row["action_text"])
            labels.append(row["label"])
    return texts, labels


def main():
    print(f"Loading dataset from {DATA_PATH} ...")
    texts, labels = load_dataset(DATA_PATH)
    print(f"Loaded {len(texts)} examples "
          f"({labels.count('safe')} safe / {labels.count('risky')} risky)")

    X_train, X_test, y_train, y_test = train_test_split(
        texts, labels, test_size=0.2, random_state=42, stratify=labels
    )

    # ngram_range=(1,2) lets the model catch two-word danger signals
    # like "force push" or "drop table", not just single words.
    vectorizer = TfidfVectorizer(
        lowercase=True,
        ngram_range=(1, 2),
        min_df=1,
        max_features=2000,
    )
    X_train_vec = vectorizer.fit_transform(X_train)
    X_test_vec = vectorizer.transform(X_test)

    clf = LogisticRegression(max_iter=1000, class_weight="balanced")
    clf.fit(X_train_vec, y_train)

    y_pred = clf.predict(X_test_vec)
    acc = accuracy_score(y_test, y_pred)
    print(f"\nTest accuracy: {acc:.3f}\n")
    print(classification_report(y_test, y_pred))

    # Retrain on the FULL dataset before saving, so the shipped model
    # benefits from every labeled example we have (common practice once
    # you've already validated performance on the held-out split above).
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

    # Show the most "risky"-weighted words/bigrams — great for the demo,
    # proves the model actually learned something explainable.
    feature_names = vectorizer.get_feature_names_out()
    # For binary LogisticRegression, clf.coef_[0] gives log-odds toward
    # clf.classes_[1] (the positive class), NOT necessarily "risky".
    # We flip the sign if "risky" is actually classes_[0], so the printed
    # words are genuinely the ones the model associates with risk.
    classes = list(clf.classes_)
    if len(classes) == 2:
        positive_class = classes[1]
        coefs = clf.coef_[0] if positive_class == "risky" else -clf.coef_[0]
    else:
        risky_index = classes.index("risky")
        coefs = clf.coef_[risky_index]
    top_risky_idx = coefs.argsort()[-15:][::-1]
    print("\nTop 15 words/phrases the model associates with RISKY:")
    for i in top_risky_idx:
        print(f"  {feature_names[i]:25s}  weight={coefs[i]:.3f}")

    # Sync to the Hugging Face repo folder if it exists alongside this repo.
    if HF_REPO_DIR.exists():
        shutil.copy(model_path, HF_REPO_DIR / MODEL_FILENAME)
        shutil.copy(vectorizer_path, HF_REPO_DIR / VECTORIZER_FILENAME)
        print(f"\nAlso synced model + vectorizer to {HF_REPO_DIR}")
    else:
        print(f"\n(Hugging Face repo folder not found at {HF_REPO_DIR} — skip sync)")


if __name__ == "__main__":
    main()
