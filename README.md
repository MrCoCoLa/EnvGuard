CLI Arguments
| Option | Description |
|---|---|
| --root <dir> | Root directory to scan (default: current directory) |
| --strict-dead | Return exit code 1 if dead variables are found in .env.example |
| --check-configs | Also check missing variables inside shell scripts, YAML, and Dockerfile |
| --filter-env-examples | Apply --include/--exclude filters to .env.example files |
| --ignore-file <path> | Path to custom ignore rules file (default: .env-ignore) |
| --include <glob> | File pattern to include in scanning (e.g. src/**, *.{ts,tsx}) |
| --exclude <glob> | File pattern to exclude from scanning |
| --summary-json <path> | Save results to a structured JSON file |
| --quiet | Silence console summaries (useful in CI scripts) |
Ignore Rules (.env-ignore)
Create a .env-ignore file in your root folder to ignore specific variables or wildcards:
AWS_*
VERCEL_*
NEXT_PUBLIC_*

Running Tests
pytest test.py -v

"""
with open("README.md", "w", encoding="utf-8") as f:
f.write(content.strip() + "\n")

Your Markdown file is ready
[file-tag: code-generated-file-501ce01c-6d81-424a-aa84-b05421ae43fc]

```markdown
# EnvGuard

Lightweight environment variable linter with zero dependencies. Keeps your code and `.env.example` in sync.

---

## Overview

`guard.py` automatically checks for environment variable mismatches across your codebase and `.env.example`:
- **Missing variables:** Flags variables used in code but absent from `.env.example`.
- **Dead variables:** Detects variables declared in `.env.example` that are never referenced anywhere in code.

Project structure:
- `guard.py` — main scanning and verification script.
- `config.py` — root discovery and test configuration (`conftest.py`).
- `test.py` — test suite (`pytest`).

---

## Requirements

- Python 3.10+
- Standard library only (no external packages required for `guard.py`)
- `pytest` (only needed to run `test.py`)

---

## Usage

Run the scanner directly from your terminal:

```bash
# Basic check
python guard.py

# Strict mode: fail with exit code 1 if dead variables exist
python guard.py --strict-dead

# Scan configuration files (.sh, .yml, Dockerfile) for missing variables
python guard.py --check-configs

# Generate a JSON summary report
python guard.py --summary-json report.json

# Run silently (suppress console output)
python guard.py --quiet

CLI Arguments
| Option | Description |
|---|---|
| --root <dir> | Root directory to scan (default: current directory) |
| --strict-dead | Return exit code 1 if dead variables are found in .env.example |
| --check-configs | Also check missing variables inside shell scripts, YAML, and Dockerfile |
| --filter-env-examples | Apply --include/--exclude filters to .env.example files |
| --ignore-file <path> | Path to custom ignore rules file (default: .env-ignore) |
| --include <glob> | File pattern to include in scanning (e.g. src/**, *.{ts,tsx}) |
| --exclude <glob> | File pattern to exclude from scanning |
| --summary-json <path> | Save results to a structured JSON file |
| --quiet | Silence console summaries (useful in CI scripts) |
Ignore Rules (.env-ignore)
Create a .env-ignore file in your root folder to ignore specific variables or wildcards:
AWS_*
VERCEL_*
NEXT_PUBLIC_*

Running Tests
pytest test.py -v