import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from scripts.env_sync_guard import (
    clear_env_caches,
    expand_curly_braces,
    extract_vars,
    get_changed_files,
    get_effective_env_vars,
    main,
    matches_glob_patterns,
    parse_env_file,
    should_scan,
    tokenize_and_strip_comments,
)


@pytest.fixture(autouse=True)
def run_around_tests():
    clear_env_caches()
    yield
    clear_env_caches()


def test_regex_after_colon_delimiter():
    source = "const obj = { endpoint: /https:\\/\\/domain\\.com/.test(url) };\nconst val = process.env.API_PORT;"
    cleaned = tokenize_and_strip_comments(source, ".ts")
    assert "/https:\\/\\/domain\\.com/" in cleaned
    assert "API_PORT" in cleaned


def test_glob_empty_class_and_character_ranges():
    assert matches_glob_patterns("file[].py", ["file[].py"]) is True
    assert matches_glob_patterns("file[!].py", ["file[!].py"]) is True
    assert matches_glob_patterns("fileA.py", ["file[!].py"]) is False
    assert matches_glob_patterns("data-5.csv", ["data-[0-9].csv"]) is True
    assert matches_glob_patterns("data-x.csv", ["data-[0-9].csv"]) is False
    assert matches_glob_patterns("data-x.csv", ["data-[!0-9].csv"]) is True


def test_expand_curly_braces_deduplication():
    res = expand_curly_braces("src/{app,app}/{index,index}.ts")
    assert res == ["src/app/index.ts"]


def test_dockerfile_variants_and_comments(tmp_path: Path):
    root = tmp_path
    df_prod = root / "Dockerfile.prod"
    df_prod.write_text("""
    # ENV COMMENTED_OUT=1
    ENV RUNTIME_URL=http://localhost
    """)
    assert should_scan(df_prod, root) is True

    extracted = [v for v, _ in extract_vars(df_prod, set())]
    assert "RUNTIME_URL" in extracted
    assert "COMMENTED_OUT" not in extracted


def test_shell_parameter_expansions_with_prefixes(tmp_path: Path):
    sh_file = tmp_path / "deploy.sh"
    sh_file.write_text("""
    echo ${APP_PORT:-8080}
    echo ${UPLOAD_DIR#/var}
    echo ${TAG//latest/stable}
    """)
    extracted = [v for v, _ in extract_vars(sh_file, set())]
    assert "APP_PORT" in extracted
    assert "UPLOAD_DIR" in extracted
    assert "TAG" in extracted


def test_get_changed_files_with_missing_sha_triggers_unshallow(tmp_path: Path):
    def fake_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        if cmd[:3] == ["git", "cat-file", "-e"]:
            res.returncode = 1
        else:
            res.returncode = 0
        return res

    with patch("subprocess.run", side_effect=fake_subprocess_run) as mock_run, \
         patch("subprocess.check_output", side_effect=[
             "true\n",            # git rev-parse --is-shallow-repository (str)
             "merge_base_sha\n",   # git merge-base (str)
             "src/index.ts\n"     # git diff --name-only (str)
         ]):
        changed = get_changed_files("abc1234def5678", tmp_path)
        assert changed is not None
        assert (tmp_path / "src" / "index.ts").resolve() in changed

        unshallow_called = any(
            call.args[0][:3] == ["git", "fetch", "--unshallow"]
            for call in mock_run.call_args_list
        )
        assert unshallow_called is True


def test_get_changed_files_with_existing_sha_skips_unshallow(tmp_path: Path):
    def fake_subprocess_run(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 0
        return res

    with patch("subprocess.run", side_effect=fake_subprocess_run) as mock_run, \
         patch("subprocess.check_output", side_effect=[
             "merge_base_sha\n",   # git merge-base (str)
             "src/index.ts\n"     # git diff --name-only (str)
         ]):
        changed = get_changed_files("abc1234def5678", tmp_path)
        assert changed is not None
        assert (tmp_path / "src" / "index.ts").resolve() in changed

        unshallow_called = any(
            call.args[0][:3] == ["git", "fetch", "--unshallow"]
            for call in mock_run.call_args_list
        )
        assert unshallow_called is False


def test_get_changed_files_merge_base_fallback(tmp_path: Path):
    with patch("subprocess.run") as mock_run, \
         patch("subprocess.check_output", side_effect=[
             subprocess.CalledProcessError(1, ["git", "merge-base"]),
             "src/index.ts\n"
         ]) as mock_check_output:
        changed = get_changed_files("origin/main", tmp_path)
        assert changed is not None
        assert (tmp_path / "src" / "index.ts").resolve() in changed

        diff_cmd = mock_check_output.call_args_list[1].args[0]
        assert "origin/main...HEAD" in diff_cmd


def test_mtime_cache_invalidation(tmp_path: Path):
    env_file = tmp_path / ".env.example"
    env_file.write_text("VAR_A=1\n")

    t1 = 1700000000.0
    os.utime(env_file, (t1, t1))

    parsed_1 = parse_env_file(env_file)
    assert "VAR_A" in parsed_1
    assert "VAR_B" not in parsed_1

    env_file.write_text("VAR_A=1\nVAR_B=2\n")
    t2 = 1700000010.0
    os.utime(env_file, (t2, t2))

    parsed_2 = parse_env_file(env_file)
    assert "VAR_B" in parsed_2


def test_check_configs_flag_enables_config_missing_detection(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("PORT=8080\n")
    (tmp_path / "deploy.sh").write_text("echo $DEPLOY_SECRET\n")

    summary_file = tmp_path / "out.json"
    with patch("scripts.env_sync_guard.get_base_git_commit", return_value=None):
        res_default = main(["--summary-json", str(summary_file), "--quiet"])
        assert res_default == 0

        res_strict = main(["--check-configs", "--summary-json", str(summary_file), "--quiet"])
        assert res_strict == 1


def test_strict_dead_exit_code(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("ORPHAN_VAR=1\n")
    (tmp_path / "app.py").write_text("print('clean')\n")

    with patch("scripts.env_sync_guard.get_base_git_commit", return_value=None):
        assert main(["--quiet"]) == 0
        assert main(["--strict-dead", "--quiet"]) == 1


def test_json_reports_dead_vars_with_line_numbers(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("# Top\nDEAD_KEY=1\n")
    summary_file = tmp_path / "out.json"

    with patch("scripts.env_sync_guard.get_base_git_commit", return_value=None):
        main(["--summary-json", str(summary_file), "--quiet"])

    data = json.loads(summary_file.read_text())
    assert data["dead_variables_count"] == 1
    assert data["dead"][".env.example"]["DEAD_KEY"] == [2]


def test_filter_env_examples_flag(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root_env = tmp_path / ".env.example"
    root_env.write_text("ROOT_VAR=1\n")

    app_dir = tmp_path / "apps" / "web"
    app_dir.mkdir(parents=True)
    (app_dir / ".env.example").write_text("APP_VAR=1\n")
    (app_dir / "index.ts").write_text("console.log(process.env.APP_VAR);")

    with patch("scripts.env_sync_guard.get_base_git_commit", return_value=None):
        # Без флага root .env.example найден -> ROOT_VAR dead
        assert main(["--strict-dead", "--include", "apps/**", "--quiet"]) == 1
        # С флагом root .env.example отсечён фильтром -> только apps/web/.env.example
        assert main(["--strict-dead", "--filter-env-examples", "--include", "apps/**", "--quiet"]) == 0


def test_effective_env_vars_hierarchy(tmp_path: Path):
    root_env = tmp_path / ".env.example"
    root_env.write_text("SHARED_VAR=1\n")

    sub_dir = tmp_path / "services" / "auth"
    sub_dir.mkdir(parents=True)
    (sub_dir / ".env.example").write_text("AUTH_SECRET=1\n")

    res = get_effective_env_vars(sub_dir, tmp_path)
    assert "SHARED_VAR" in res
    assert "AUTH_SECRET" in res


def test_relative_ignore_file(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("AWS_TOKEN=1\n")
    (tmp_path / "custom.ignore").write_text("AWS_*\n")
    (tmp_path / "app.py").write_text("print('clean')\n")

    with patch("scripts.env_sync_guard.get_base_git_commit", return_value=None):
        exit_code = main(["--strict-dead", "--ignore-file", "custom.ignore", "--quiet"])
        assert exit_code == 0