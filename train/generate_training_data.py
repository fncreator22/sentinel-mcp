"""
train/generate_training_data.py
================================
Uses your locally running Ollama model to synthetically generate new
labeled training examples for the Stage 2 risk classifier.

For each category, it sends a structured prompt to Ollama and asks it to
produce N lines in CSV format (action_text,label). The output is validated,
deduplicated, and appended to data/training_examples.csv.

USAGE:
    python train/generate_training_data.py

The script requires Ollama to be running on localhost:11434.
It will print progress as it generates each batch.
"""

import csv
import json
import urllib.request
import urllib.error
import time
from pathlib import Path

# ---- Config ----------------------------------------------------------------
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5-coder:7b"
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "training_examples.csv"
EXAMPLES_PER_BATCH = 15   # smaller batches = shorter prompts = faster response from a large model

# Each tuple: (category_name, prompt_description, safe_or_risky_or_both)
CATEGORIES = [
    (
        "file_credential_exposure",
        "Linux/macOS shell commands that read or expose secret files, private keys, API keys, "
        "passwords, or environment variables. Include both obvious cases (cat .env) and subtle "
        "ones (python -c 'import os; print(os.environ)'). All should be labeled risky.",
        "risky",
    ),
    (
        "safe_file_operations",
        "Safe, read-only or non-destructive file operations a developer would normally run: "
        "listing files, reading source code, checking sizes, diffing, copying example configs. "
        "None should expose secrets or delete data. All should be labeled safe.",
        "safe",
    ),
    (
        "destructive_shell",
        "Shell commands that permanently destroy data, wipe disks, remove entire directories, "
        "format volumes, or kill critical system processes. Include rm -rf variants, dd, shred, "
        "wipefs, and process kills against system PIDs. All should be labeled risky.",
        "risky",
    ),
    (
        "safe_devops",
        "Routine DevOps and infrastructure commands that are non-destructive: checking status, "
        "viewing logs, listing resources, running health checks, applying non-breaking config. "
        "Examples: kubectl get pods, docker ps, terraform plan, aws describe-instances. "
        "All labeled safe.",
        "safe",
    ),
    (
        "sql_injection_and_destructive_sql",
        "SQL statements that are dangerous: SQL injection payloads, DROP TABLE, DELETE without "
        "WHERE, TRUNCATE, UPDATE all rows, GRANT all privileges, mass data exfiltration via "
        "UNION SELECT. All labeled risky.",
        "risky",
    ),
    (
        "safe_sql",
        "Routine, safe SQL queries: SELECT with specific WHERE clauses, INSERT of a single row, "
        "CREATE TABLE, CREATE INDEX, EXPLAIN, BEGIN/COMMIT, SELECT COUNT(*). "
        "All labeled safe.",
        "safe",
    ),
    (
        "network_attack_and_exfiltration",
        "Commands that scan networks aggressively, perform ARP spoofing, launch reverse shells, "
        "exfiltrate data via curl/nc/socat to attacker-controlled servers, or tunnel traffic. "
        "All labeled risky.",
        "risky",
    ),
    (
        "safe_network",
        "Standard non-aggressive network diagnostic commands: ping, curl to a public API, "
        "wget a public file, nslookup, dig, traceroute, checking open ports on localhost, "
        "curl health checks to known endpoints. All labeled safe.",
        "safe",
    ),
    (
        "cloud_privilege_escalation",
        "AWS/GCP/Azure CLI commands that escalate privileges, make resources public, delete "
        "production infrastructure, create backdoor IAM users/keys, or remove audit logs. "
        "All labeled risky.",
        "risky",
    ),
    (
        "safe_cloud",
        "Read-only or non-breaking cloud CLI commands: listing resources, describing "
        "configurations, checking IAM roles, viewing logs, running plans. "
        "All labeled safe.",
        "safe",
    ),
    (
        "docker_and_k8s_escape",
        "Docker or Kubernetes commands that attempt container escape: mounting host filesystem, "
        "running privileged containers, mounting docker socket, accessing metadata API from "
        "within a container, creating cluster admin bindings. All labeled risky.",
        "risky",
    ),
    (
        "safe_containers",
        "Standard, non-privileged container management: docker build, docker ps, docker logs, "
        "kubectl get, kubectl describe, kubectl apply to safe yaml files, helm install. "
        "All labeled safe.",
        "safe",
    ),
    (
        "malware_and_backdoor_delivery",
        "Commands that download and execute unknown scripts (curl | bash), obfuscated payloads "
        "(base64 decode then exec), install reverse shells, add unauthorized SSH keys, "
        "create hidden cron jobs or startup entries, or install known attack tools. "
        "All labeled risky.",
        "risky",
    ),
    (
        "safe_package_management",
        "Standard package installation from known, reputable sources: pip install well-known-lib, "
        "npm install known-package, apt-get install standard-tool, brew install utility. "
        "All labeled safe.",
        "safe",
    ),
    (
        "git_risky",
        "Risky git operations: force push to main/master, rewriting commit history to remove "
        "audit trails, leaking credentials in commit messages, publishing a .env to a public "
        "repo, git filter-branch to tamper with history. All labeled risky.",
        "risky",
    ),
    (
        "git_safe",
        "Routine, safe git operations: git status, git log, git diff, git add, git commit, "
        "git push to feature branches, git clone of public repos, git stash, git merge. "
        "All labeled safe.",
        "safe",
    ),
    (
        "system_privilege_abuse",
        "Commands that abuse root or sudo access: switching to root, adding new sudo users, "
        "disabling SELinux or AppArmor, clearing audit logs, modifying /etc/sudoers, "
        "changing passwords for system accounts, disabling firewalls. All labeled risky.",
        "risky",
    ),
    (
        "safe_testing_and_ci",
        "Safe test-running and CI/CD commands: pytest, jest, go test, cargo test, mvn test, "
        "running linters, formatters, static analysis tools, coverage reporters. "
        "All labeled safe.",
        "safe",
    ),
]


def load_existing(path: Path) -> set:
    """Return the set of action_text values already in the CSV."""
    existing = set()
    if not path.exists():
        return existing
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            existing.add(row["action_text"].strip())
    return existing


def ask_ollama(prompt: str) -> str:
    """
    Call Ollama with stream=True so each token arrives immediately.
    This prevents socket timeouts on large slow models (e.g. 32B).
    The 30-second socket timeout only applies to the gap *between* tokens,
    not the total generation time.
    """
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": 0.85,
            "top_p": 0.95,
            "num_predict": 1024,
        },
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    full_response = []
    # 180s timeout: covers the time Ollama needs to load the 32B model into
    # memory before the first token is sent. Once streaming starts, tokens
    # arrive within seconds of each other, well within this window.
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw_line in resp:
            raw_line = raw_line.decode("utf-8").strip()
            if not raw_line:
                continue
            try:
                chunk = json.loads(raw_line)
                full_response.append(chunk.get("response", ""))
                if chunk.get("done"):
                    break
            except json.JSONDecodeError:
                continue

    return "".join(full_response)


def build_prompt(category: str, description: str, label: str, n: int) -> str:
    label_instruction = (
        f"All examples must be labeled '{label}'."
        if label in ("safe", "risky")
        else "Mix labels: roughly half safe, half risky."
    )

    return f"""You are a security dataset generator for an AI guardrail classifier.

Generate exactly {n} realistic training examples for the category: {category}

Category description: {description}

{label_instruction}

Rules:
- Each line must be a valid CSV row: action_text,label
- action_text is a realistic command, SQL statement, or API call an AI coding agent might produce
- label is either 'safe' or 'risky' (lowercase, no quotes)
- Do NOT include a header row
- Do NOT include any explanation, commentary, numbering, or markdown
- Do NOT use double-quotes unless the action_text contains a comma (then wrap the whole action_text in double-quotes)
- Generate diverse examples — vary tools, flags, targets, and contexts
- Make risky examples look realistic and not obviously malicious (the model must learn subtle patterns)

Output only the CSV lines, nothing else:
"""


def parse_ollama_csv(raw: str, expected_label: str) -> list[tuple[str, str]]:
    """Parse LLM output into (action_text, label) tuples, filtering bad rows."""
    results = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # Remove markdown code fences if the model added them
        if line.startswith("```"):
            continue

        # Try to split on the LAST comma to get label
        parts = line.rsplit(",", 1)
        if len(parts) != 2:
            continue

        action_text = parts[0].strip().strip('"')
        label = parts[1].strip().strip('"').lower()

        if label not in ("safe", "risky"):
            continue

        # If we specified an expected label and the model contradicted it, trust our spec
        if expected_label in ("safe", "risky") and label != expected_label:
            label = expected_label

        if action_text:
            results.append((action_text, label))

    return results


def append_to_csv(path: Path, rows: list[tuple[str, str]]):
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for action_text, label in rows:
            writer.writerow([action_text, label])


def main():
    print(f"Loading existing examples from {DATA_PATH} ...")
    existing = load_existing(DATA_PATH)
    print(f"Found {len(existing)} existing examples.\n")

    total_added = 0

    for category, description, label in CATEGORIES:
        print(f"Generating batch: [{category}] ({EXAMPLES_PER_BATCH} examples, label={label}) ...")

        prompt = build_prompt(category, description, label, EXAMPLES_PER_BATCH)

        try:
            raw = ask_ollama(prompt)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  ERROR calling Ollama: {e}. Skipping this category.")
            continue

        parsed = parse_ollama_csv(raw, label)

        # Deduplicate against existing + already generated in this run
        new_rows = []
        for action_text, lbl in parsed:
            if action_text not in existing:
                new_rows.append((action_text, lbl))
                existing.add(action_text)

        if new_rows:
            append_to_csv(DATA_PATH, new_rows)
            total_added += len(new_rows)
            print(f"  Added {len(new_rows)} new examples (skipped {len(parsed) - len(new_rows)} duplicates).")
        else:
            print(f"  No new unique examples generated for this category.")

        # Small pause between requests so Ollama doesn't get overwhelmed
        time.sleep(1)

    print(f"\nDone. Total new examples added: {total_added}")
    print(f"New dataset size: {len(existing)} examples.")
    print("\nNext step: run the training script to retrain the classifier:")
    print("  python train/train_classifier.py")


if __name__ == "__main__":
    main()
