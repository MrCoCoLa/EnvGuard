import argparse
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

CODE_EXTENSIONS = {
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".py",
    ".go",
    ".rs",
    ".java",
    ".cs",
    ".rb",
    ".php",
}

CONFIG_EXTENSIONS = {
    ".yml", ".yaml", ".toml",
    ".sh", ".bash", ".zsh",
    ".dockerfile",
}

ALL_SCANNABLE_EXTENSIONS = CODE_EXTENSIONS | CONFIG_EXTENSIONS

IGNORE_DIR_NAMES = {
    ".git", ".github", "node_modules", "vendor", "dist",
    "build", "coverage", "__pycache__", "venv", ".venv",
    "bin", "obj", "target", "__mocks__", "fixtures", "__fixtures__", "e2e",
}

IGNORE_FILE_PATTERNS = [
    re.compile(r"\.(test|spec)\.[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"^(test_|mock_)", re.IGNORECASE),
    re.compile(r"_(test|mock)\.[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"\.snap$", re.IGNORECASE),
]

DEFAULT_SYSTEM_VARS = {
    "NODE_ENV", "PORT", "HOST", "PATH", "HOME", "USER",
    "SHELL", "PWD", "CI", "PYTHONPATH", "LANG", "TZ",
    "TMPDIR", "EDITOR", "TERM", "COLUMNS", "LINES",
    "DEBUG", "LOG_LEVEL", "APP_ENV", "ENVIRONMENT",
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "PIP_NO_CACHE_DIR", "NPM_CONFIG_LOGLEVEL", "DENO_DIR", "BUN_INSTALL",
}

CODE_PATTERNS = [
    re.compile(r"\bprocess\.env(?:\.([A-Za-z0-9_]{2,})|\[['\"]([A-Za-z0-9_]{2,})['\"]\])"),
    re.compile(r"\bimport\.meta\.env(?:\.([A-Za-z0-9_]{2,})|\[['\"]([A-Za-z0-9_]{2,})['\"]\])"),
    re.compile(r"\bDeno\.env\.get\(\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\)"),
    re.compile(r"\bos\.(?:getenv|environ\.get|environ\.setdefault)\(\s*['\"]([A-Za-z0-9_]{2,})['\"]"),
    re.compile(r"\bos\.environ\[\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\]"),
    re.compile(r"\bos\.(?:Getenv|LookupEnv)\(\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\)"),
    re.compile(r"\b(?:std::)?env::var\s*\(\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\)"),
    re.compile(r"\bSystem\.getenv\(\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\)"),
    re.compile(r"\bEnvironment\.GetEnvironmentVariable\(\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\)"),
    re.compile(r"\bENV(?:\[\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\]|\.fetch\(\s*['\"]([A-Za-z0-9_]{2,})['\"])"),
    re.compile(r"\bgetenv\(\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\)"),
    re.compile(r"\$(?:_ENV|_SERVER)\[\s*['\"]([A-Za-z0-9_]{2,})['\"]\s*\]"),
]

CONFIG_PATTERNS = [
    re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]{1,})(?:[^}]*)?\}"),
    re.compile(r"(?<![\w$])\$([A-Za-z_][A-Za-z0-9_]{1,})\b"),
    re.compile(r"\$ENV\{([A-Za-z_][A-Za-z0-9_]{1,})\}"),
    re.compile(r"\bENV\s+([A-Za-z_][A-Za-z0-9_]{1,})(?:=|\s)", re.IGNORECASE),
]

ENV_DECLARATION_PATTERN = re.compile(
    r"^(?:#\s*)?(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*="
)

_ENV_FILE_CACHE: dict[Path, tuple[float, dict[str, int]]] = {}
_EFFECTIVE_ENV_CACHE: dict[tuple[Path, Path], tuple[tuple[float, ...], frozenset[str]]] = {}


def clear_env_caches() -> None:
    _ENV_FILE_CACHE.clear()
    _EFFECTIVE_ENV_CACHE.clear()
    glob_to_regex.cache_clear()


def normalize_path(path: Path) -> Path:
    return Path(os.path.realpath(path))


def get_file_ext(path: Path) -> str:
    name_lower = path.name.lower()
    if name_lower.startswith("dockerfile"):
        return ".dockerfile"
    return path.suffix.lower()


def expand_curly_braces(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]+)\}", pattern)
    if not match:
        return [pattern]
    front = pattern[:match.start()]
    back = pattern[match.end():]
    parts = match.group(1).split(",")
    results = []
    for part in parts:
        for expanded in expand_curly_braces(f"{front}{part}{back}"):
            results.append(expanded)
    return list(dict.fromkeys(results))


@lru_cache(maxsize=256)
def glob_to_regex(pattern: str) -> re.Pattern:
    i, n = 0, len(pattern)
    res = []
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    res.append("(?:.*/)?")
                    i += 3
                else:
                    res.append(".*")
                    i += 2
            else:
                res.append("[^/]*")
                i += 1
        elif c == "?":
            res.append("[^/]")
            i += 1
        elif c == "[":
            end_bracket = pattern.find("]", i + 1)
            if end_bracket != -1:
                inner = pattern[i + 1:end_bracket]
                if not inner:
                    res.append(r"\[\]")
                elif inner == "!":
                    res.append(r"\[!\]")
                elif inner.startswith("!"):
                    res.append(f"[^{inner[1:]}]")
                else:
                    res.append(f"[{inner}]")
                i = end_bracket + 1
            else:
                res.append(r"\[")
                i += 1
        elif c in r"(){}+$^.|":
            res.append("\\" + c)
            i += 1
        else:
            res.append(c)
            i += 1
    return re.compile(f"^{''.join(res)}$")


def matches_glob_patterns(rel_posix: str, patterns: list[str]) -> bool:
    for pat in patterns:
        clean_pat = pat.removeprefix("./")
        for expanded_pat in expand_curly_braces(clean_pat):
            regex = glob_to_regex(expanded_pat)
            if regex.match(rel_posix):
                return True
            if not expanded_pat.startswith("**/") and glob_to_regex(f"**/{expanded_pat}").match(rel_posix):
                return True
    return False


def load_user_ignores(root_dir: Path, custom_ignore_file: Path | None = None) -> set[str]:
    target_file = custom_ignore_file or (root_dir / ".env-ignore")
    ignores = {var.upper() for var in DEFAULT_SYSTEM_VARS}
    if target_file and target_file.is_file():
        for line in target_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ignores.add(line.upper())
    return ignores


def is_ignored(var_name: str, ignore_rules: set[str]) -> bool:
    upper = var_name.upper()
    for rule in ignore_rules:
        if fnmatch.fnmatchcase(upper, rule):
            return True
    return False


def get_base_git_commit() -> str | None:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).is_file():
        try:
            with open(event_path, "r", encoding="utf-8") as f:
                event_data = json.load(f)
                base_sha = event_data.get("pull_request", {}).get("base", {}).get("sha")
                if base_sha:
                    return base_sha
                before_sha = event_data.get("before")
                if before_sha and before_sha != "0000000000000000000000000000000000000000":
                    return before_sha
        except Exception:
            pass

    base_ref = os.getenv("GITHUB_BASE_REF")
    if base_ref:
        return f"origin/{base_ref}"

    try:
        remote_head = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if remote_head and remote_head != "HEAD":
            return remote_head
    except subprocess.CalledProcessError:
        pass

    try:
        subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD~1"],
            stderr=subprocess.DEVNULL,
        )
        return "HEAD~1"
    except subprocess.CalledProcessError:
        return None


def get_changed_files(base_commit: str, root_dir: Path) -> set[Path] | None:
    if base_commit.startswith("origin/"):
        branch_name = base_commit.removeprefix("origin/")
        subprocess.run(
            ["git", "fetch", "--depth=1", "--no-tags", "origin", branch_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        has_commit = subprocess.run(
            ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0

        if not has_commit:
            ref_to_fetch = os.getenv("GITHUB_BASE_REF")
            if ref_to_fetch:
                subprocess.run(
                    ["git", "fetch", "--depth=50", "--no-tags", "origin", ref_to_fetch],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                try:
                    is_shallow = subprocess.check_output(
                        ["git", "rev-parse", "--is-shallow-repository"],
                        stderr=subprocess.DEVNULL,
                        text=True,
                    ).strip()
                    if is_shallow == "true":
                        subprocess.run(
                            ["git", "fetch", "--unshallow"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                except subprocess.CalledProcessError:
                    pass

    diff_target = base_commit
    try:
        merge_base = subprocess.check_output(
            ["git", "merge-base", base_commit, "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if merge_base:
            diff_target = merge_base
    except subprocess.CalledProcessError:
        pass

    try:
        output = subprocess.check_output(
            ["git", "diff", "--name-only", f"{diff_target}...HEAD"],
            stderr=subprocess.PIPE,
            text=True,
        )
        changed = set()
        for line in output.splitlines():
            cleaned = line.strip()
            if cleaned:
                changed.add(normalize_path(root_dir / cleaned))
        return changed
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"::warning::git diff failed: {e.stderr.strip()}\n")
        return None


def should_scan(path: Path, root_dir: Path, includes: list[str] | None = None, excludes: list[str] | None = None) -> bool:
    ext = get_file_ext(path)

    try:
        rel_posix = path.relative_to(root_dir).as_posix()
    except ValueError:
        rel_posix = path.as_posix()

    if includes and not matches_glob_patterns(rel_posix, includes):
        return False

    if excludes and matches_glob_patterns(rel_posix, excludes):
        return False

    if ext not in ALL_SCANNABLE_EXTENSIONS:
        return False

    name_lower = path.name.lower()
    for pat in IGNORE_FILE_PATTERNS:
        if pat.search(name_lower):
            return False
    return True


def tokenize_and_strip_comments(content: str, ext: str) -> str:
    is_hash_lang = ext in {".py", ".rb", ".sh", ".bash", ".zsh", ".yaml", ".yml", ".dockerfile"}
    is_python_or_ruby = ext in {".py", ".rb"}
    is_js_ts = ext in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
    result = []
    i = 0
    n = len(content)

    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    in_triple_single = False
    in_triple_double = False
    in_line_comment = False
    in_block_comment = False
    in_regex_literal = False

    last_token = ""
    current_token_chars = []
    token_delimiters = "=(,[{:;!&|?~^\n<>)]{}:"

    while i < n:
        char = content[i]
        nxt = content[i + 1] if i + 1 < n else ""
        nxt2 = content[i + 2] if i + 2 < n else ""
        prev = content[i - 1] if i > 0 else ""

        if char.isspace():
            if current_token_chars:
                last_token = "".join(current_token_chars)
                current_token_chars = []
        elif not in_line_comment and not in_block_comment:
            if char in token_delimiters:
                if current_token_chars:
                    last_token = "".join(current_token_chars)
                    current_token_chars = []
                last_token = char
            elif char.isalnum() or char in "_$":
                current_token_chars.append(char)

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                result.append(char)
            i += 1
            continue

        if in_block_comment:
            if char == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if in_regex_literal:
            if char == "\n":
                in_regex_literal = False
                result.append(char)
                i += 1
                continue
            if char == "\\" and i + 1 < n:
                result.append(char)
                result.append(content[i + 1])
                i += 2
                continue
            if char == "/":
                in_regex_literal = False
            result.append(char)
            i += 1
            continue

        if is_python_or_ruby and not in_single_quote and not in_double_quote:
            if in_triple_double:
                if char == '"' and nxt == '"' and nxt2 == '"':
                    in_triple_double = False
                    i += 3
                else:
                    i += 1
                continue
            if in_triple_single:
                if char == "'" and nxt == "'" and nxt2 == "'":
                    in_triple_single = False
                    i += 3
                else:
                    i += 1
                continue
            if char == '"' and nxt == '"' and nxt2 == '"':
                in_triple_double = True
                i += 3
                continue
            if char == "'" and nxt == "'" and nxt2 == "'":
                in_triple_single = True
                i += 3
                continue

        if not in_single_quote and not in_double_quote and not in_backtick:
            if char == "/" and nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if char == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue

            if is_js_ts and char == "/":
                prev_tok = "".join(current_token_chars) or last_token
                is_regex_prefix = (
                    not prev_tok or
                    prev_tok[-1] in token_delimiters or
                    prev_tok in {"return", "yield", "case"}
                )
                if is_regex_prefix:
                    in_regex_literal = True
                    result.append(char)
                    i += 1
                    continue

            if char == "#" and is_hash_lang:
                if is_python_or_ruby:
                    in_line_comment = True
                    i += 1
                    continue
                if prev and not prev.isspace():
                    result.append(char)
                    i += 1
                    continue
                in_line_comment = True
                i += 1
                continue

        if char == "\\" and (in_single_quote or in_double_quote or in_backtick):
            result.append(char)
            if i + 1 < n:
                result.append(content[i + 1])
                i += 2
            else:
                i += 1
            continue

        if char == "'" and not in_double_quote and not in_backtick:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote and not in_backtick:
            in_double_quote = not in_double_quote
        elif char == "`" and not in_single_quote and not in_double_quote:
            in_backtick = not in_backtick

        result.append(char)
        i += 1

    return "".join(result)


def parse_env_file(path: Path) -> dict[str, int]:
    normalized = normalize_path(path)
    if not normalized.is_file():
        return {}

    try:
        current_mtime = normalized.stat().st_mtime
    except OSError:
        return {}

    if normalized in _ENV_FILE_CACHE:
        cached_mtime, cached_data = _ENV_FILE_CACHE[normalized]
        if cached_mtime == current_mtime:
            return cached_data

    declarations = {}
    try:
        content = normalized.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    for line_num, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        match = ENV_DECLARATION_PATTERN.match(line)
        if match:
            declarations[match.group(1).upper()] = line_num

    _ENV_FILE_CACHE[normalized] = (current_mtime, declarations)
    return declarations


def get_effective_env_vars(directory: Path, root_dir: Path) -> frozenset[str]:
    curr = normalize_path(directory)
    root_resolved = normalize_path(root_dir)
    cache_key = (curr, root_resolved)

    hierarchy = []
    scan_ptr = curr
    while True:
        candidate = scan_ptr / ".env.example"
        if candidate.is_file():
            hierarchy.append(candidate)
        if scan_ptr == root_resolved or scan_ptr.parent == scan_ptr:
            break
        scan_ptr = scan_ptr.parent

    mtimes = []
    for p in hierarchy:
        try:
            mtimes.append(p.stat().st_mtime)
        except OSError:
            pass
    current_mtimes = tuple(mtimes)

    if cache_key in _EFFECTIVE_ENV_CACHE:
        cached_mtimes, cached_vars = _EFFECTIVE_ENV_CACHE[cache_key]
        if cached_mtimes == current_mtimes:
            return cached_vars

    merged = set()
    for env_path in reversed(hierarchy):
        merged.update(parse_env_file(env_path).keys())

    res = frozenset(merged)
    _EFFECTIVE_ENV_CACHE[cache_key] = (current_mtimes, res)
    return res


def extract_vars(path: Path, ignore_rules: set[str]) -> list[tuple[str, int]]:
    results = []
    ext = get_file_ext(path)

    try:
        raw_text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return results

    cleaned_text = tokenize_and_strip_comments(raw_text, ext)

    if ext in CODE_EXTENSIONS:
        for line_num, line in enumerate(cleaned_text.splitlines(), start=1):
            for pattern in CODE_PATTERNS:
                for match in pattern.finditer(line):
                    for group in match.groups():
                        if group and not is_ignored(group, ignore_rules):
                            results.append((group, line_num))
    elif ext in CONFIG_EXTENSIONS:
        for line_num, line in enumerate(cleaned_text.splitlines(), start=1):
            for pattern in CONFIG_PATTERNS:
                for match in pattern.finditer(line):
                    for group in match.groups():
                        if group and not is_ignored(group, ignore_rules):
                            results.append((group, line_num))

    return results


def discover_files(root_dir: Path, includes: list[str], excludes: list[str], filter_env_examples: bool = False) -> tuple[list[Path], list[Path]]:
    target_files = []
    env_examples = []

    for root, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIR_NAMES]
        current_dir = Path(root)

        for filename in filenames:
            file_path = normalize_path(current_dir / filename)
            if filename == ".env.example":
                if filter_env_examples and not should_scan(file_path, root_dir, includes, excludes):
                    continue
                env_examples.append(file_path)
            elif should_scan(file_path, root_dir, includes, excludes):
                target_files.append(file_path)

    return target_files, sorted(env_examples)


def write_summary_json(
    path: Path,
    root_dir: Path,
    base_commit: str | None,
    missing_vars: dict,
    dead_vars: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    all_unique_dead = {var for v in dead_vars.values() for var, _ in v}
    json_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "base_commit": base_commit,
        "missing_variables_count": len(missing_vars),
        "dead_variables_count": len(all_unique_dead),
        "missing": {
            k: {f: sorted(set(lines)) for f, lines in v.items()}
            for k, v in missing_vars.items()
        },
        "dead": {
            env_file: {
                var: sorted(set(ln for v_name, ln in pairs if v_name == var))
                for var in sorted({v_name for v_name, _ in pairs})
            }
            for env_file, pairs in dead_vars.items()
        },
    }
    path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EnvSync Guard")
    parser.add_argument("--root", type=Path, default=None, help="Root directory to scan")
    parser.add_argument("--strict-dead", "--fail-on-dead", action="store_true", dest="strict_dead",
                        help="Fail pipeline if dead/unused variables are found")
    parser.add_argument("--check-configs", action="store_true", help="Check missing env vars in config files (.sh, .yml, Dockerfile) as well")
    parser.add_argument("--filter-env-examples", action="store_true", help="Apply --include/--exclude patterns to .env.example files")
    parser.add_argument("--ignore-file", type=Path, default=None, help="Path to custom ignore file")
    parser.add_argument("--include", action="append", default=[], help="File glob pattern to include")
    parser.add_argument("--exclude", action="append", default=[], help="File glob pattern to exclude")
    parser.add_argument("--summary-json", type=Path, default=None, help="Output summary in JSON format")
    parser.add_argument("--quiet", action="store_true", help="Suppress console stdout summaries")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    root_dir = normalize_path(args.root or Path.cwd())

    ignore_file_path = args.ignore_file
    if ignore_file_path and not ignore_file_path.is_absolute():
        ignore_file_path = root_dir / ignore_file_path

    if ignore_file_path and not ignore_file_path.is_file():
        parser.error(f"Provided --ignore-file '{ignore_file_path}' does not exist.")

    all_target_files, all_env_examples = discover_files(
        root_dir, args.include, args.exclude, filter_env_examples=args.filter_env_examples
    )

    base_commit = get_base_git_commit()
    changed_files = get_changed_files(base_commit, root_dir) if base_commit else None

    if not all_env_examples:
        print("::error::No '.env.example' files were found anywhere in the repository.")
        if args.summary_json:
            write_summary_json(args.summary_json, root_dir, base_commit, {}, {})
        return 1

    ignore_rules = load_user_ignores(root_dir, ignore_file_path)

    file_variables_map: dict[Path, list[tuple[str, int]]] = {}
    all_repo_code_vars: set[str] = set()

    for file_path in all_target_files:
        extracted = extract_vars(file_path, ignore_rules)
        file_variables_map[file_path] = extracted
        for var_name, _ in extracted:
            all_repo_code_vars.add(var_name.upper())

    missing_vars: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for file_path, var_entries in file_variables_map.items():
        ext = get_file_ext(file_path)
        is_scannable = ext in CODE_EXTENSIONS or (args.check_configs and ext in CONFIG_EXTENSIONS)
        if not is_scannable:
            continue
        if changed_files is not None and file_path not in changed_files:
            continue

        available_env = get_effective_env_vars(file_path.parent, root_dir)
        try:
            rel_path_str = str(file_path.relative_to(root_dir))
        except ValueError:
            rel_path_str = str(file_path)

        for var_name, line_num in var_entries:
            if var_name.upper() not in available_env:
                missing_vars[var_name][rel_path_str].append(line_num)

    dead_vars: dict[str, list[tuple[str, int]]] = defaultdict(list)
    unique_dead_vars: set[str] = set()

    for env_path in all_env_examples:
        declared_map = parse_env_file(env_path)
        try:
            rel_env_path = str(env_path.relative_to(root_dir))
        except ValueError:
            rel_env_path = str(env_path)

        for var_name, line_num in sorted(declared_map.items()):
            if var_name not in all_repo_code_vars and not is_ignored(var_name, ignore_rules):
                dead_vars[rel_env_path].append((var_name, line_num))
                unique_dead_vars.add(var_name)

    total_missing_vars_count = len(missing_vars)
    total_dead_vars_count = len(unique_dead_vars)

    for var_name, files_map in sorted(missing_vars.items()):
        for rel_file, lines in sorted(files_map.items()):
            sorted_lines = sorted(set(lines))
            line_list_str = ",".join(map(str, sorted_lines))
            first_line = sorted_lines[0]
            print(f"::error file={rel_file},line={first_line}::Variable '{var_name}' is not in .env.example (lines: {line_list_str})")

    for env_file, unused_list in sorted(dead_vars.items()):
        for var_name, line_num in unused_list:
            print(f"::warning file={env_file},line={line_num}::Variable '{var_name}' declared in .env.example is never referenced in code.")

    if not args.quiet:
        print("\nENVSYNC VALIDATION SUMMARY")
        print(f"Total unique missing variables: {total_missing_vars_count}")
        print(f"Total unique dead variables: {total_dead_vars_count}")

    if args.summary_json:
        write_summary_json(args.summary_json, root_dir, base_commit, missing_vars, dead_vars)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("# EnvSync Guard Summary\n\n")
            if not missing_vars and not dead_vars:
                f.write("All environment variables are in sync.\n")
            if missing_vars:
                f.write(f"### Missing Variables ({total_missing_vars_count})\n\n| Variable | Locations |\n| :--- | :--- |\n")
                for var_name, files_map in sorted(missing_vars.items()):
                    loc_strs = [f"`{f}:{','.join(map(str, sorted(set(lns))))}`" for f, lns in files_map.items()]
                    f.write(f"| **`{var_name}`** | {'<br>'.join(loc_strs)} |\n")
            if dead_vars:
                f.write(f"\n### Dead Variables ({total_dead_vars_count})\n\n| File | Line | Variable |\n| :--- | :--- | :--- |\n")
                for env_file, unused_list in sorted(dead_vars.items()):
                    for var_name, line_num in unused_list:
                        f.write(f"| `{env_file}` | {line_num} | `{var_name}` |\n")

    if missing_vars:
        return 1
    if args.strict_dead and dead_vars:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())