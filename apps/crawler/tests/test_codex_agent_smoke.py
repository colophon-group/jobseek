from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/codex-agent-smoke.py"


def _module():
    spec = importlib.util.spec_from_file_location("codex_agent_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _session(home: Path, name: str, cwd: str, *, old: bool = True) -> Path:
    path = home / "sessions/2026/09/28" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "cwd": cwd,
                    "source": "exec",
                    "originator": "codex_exec",
                },
            }
        )
        + "\n"
    )
    if old:
        os.utime(path, (time.time() - 7200, time.time() - 7200))
    return path


def test_legacy_archival_preserves_bytes_manifest_and_unrelated_sessions(tmp_path: Path) -> None:
    home = tmp_path.resolve() / "home"
    smoke = _session(home, "smoke.jsonl", "/tmp/jobseek-codex-smoke-abcdefgh")
    data = smoke.read_bytes()
    unrelated = _session(home, "manual.jsonl", "/tmp/manual")
    resolver = _session(
        home, "resolver.jsonl", "/srv/jobseek-codex/worktrees/company-request-1-issue-1-123-abc"
    )
    recent = _session(home, "recent.jsonl", "/tmp/jobseek-codex-smoke-12345678", old=False)
    linked = smoke.parent / "symlink.jsonl"
    linked.symlink_to(unrelated)

    assert _module().archive_legacy_smoke_sessions(home) == 1
    assert not smoke.exists()
    assert all(path.exists() for path in (unrelated, resolver, recent, linked))
    manifest_path = next((home / "smoke-session-archive").glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["sha256"] == hashlib.sha256(data).hexdigest()
    assert (manifest_path.parent / manifest["archive"]).read_bytes() == data
    assert _module().archive_legacy_smoke_sessions(home) == 0


def test_legacy_archival_refuses_archive_directory_symlink(tmp_path: Path) -> None:
    home = tmp_path.resolve() / "home"
    smoke = _session(home, "smoke.jsonl", "/tmp/jobseek-codex-smoke-abcdefgh")
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "smoke-session-archive").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="unsafe"):
        _module().archive_legacy_smoke_sessions(home)
    assert smoke.exists()
    assert list(outside.iterdir()) == []


def test_legacy_archival_retries_after_archive_link_was_created(tmp_path: Path) -> None:
    home = tmp_path.resolve() / "home"
    smoke = _session(home, "smoke.jsonl", "/tmp/jobseek-codex-smoke-abcdefgh")
    archive = home / "smoke-session-archive"
    archive.mkdir()
    name = hashlib.sha256(smoke.read_bytes()).hexdigest() + "-" + smoke.name
    os.link(smoke, archive / name)
    assert _module().archive_legacy_smoke_sessions(home) == 1
    assert not smoke.exists()
    assert (archive / name).is_file()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_smoke_native_sessions_are_disposable_on_success_and_failure(
    tmp_path: Path, monkeypatch, exit_code: int
) -> None:
    home = tmp_path.resolve() / "persistent-home"
    home.mkdir()
    (home / "auth.json").write_text("fixture-auth")
    sentinel = _session(
        home, "resolver.jsonl", "/srv/jobseek-codex/worktrees/company-request-1-issue-1-123-abc"
    )
    observed = tmp_path / "observed.json"
    fake = tmp_path / "fake-codex"
    fake.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
home = Path(os.environ["CODEX_HOME"])
fixture = Path(sys.argv[sys.argv.index("-C")+1])
assert home != Path({str(home)!r})
assert os.readlink(home / "auth.json") == {str(home / "auth.json")!r}
Path({str(observed)!r}).write_text(json.dumps({{"home":str(home), "fixture":str(fixture)}}))
sessions = home / "sessions"
sessions.mkdir()
thread = "11111111-1111-1111-1111-111111111111"
arguments = {{"agent_type":"jobseek-labeller-normalizer", "text":"a\\u2028b"}}
payload = {{"type":"function_call", "name":"spawn_agent",
           "arguments":json.dumps(arguments, ensure_ascii=False)}}
record = json.dumps({{"payload":payload}}, ensure_ascii=False)
(sessions / (thread + ".jsonl")).write_text(record+"\\n")
(sessions / "child.jsonl").write_text("{{}}\\n")
(fixture / "result.html").write_text("<p>Jobseek smoke fixture</p>")
print(json.dumps({{"type":"thread.started", "thread_id":thread}}))
print(json.dumps({{"type":"turn.completed"}}))
sys.exit({exit_code})
""")
    fake.chmod(0o755)
    monkeypatch.setenv("CODEX_HOME", str(home))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--codex", str(fake)], capture_output=True, text=True
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    paths = json.loads(observed.read_text())
    assert not Path(paths["home"]).exists()
    assert not Path(paths["fixture"]).exists()
    assert list((home / "sessions/2026/09/28").iterdir()) == [sentinel]


def test_deploy_archives_legacy_smokes_inside_the_runner_lock() -> None:
    deploy = (ROOT / "scripts/deploy-codex-runner-host.sh").read_text()
    main = deploy[deploy.index("main() {") :]
    assert main.index('flock -w "${LOCK_TIMEOUT_S}" 9') < main.index("sync_crawler_runtime")
    assert (
        main.index("sync_crawler_runtime")
        < main.index("--archive-legacy-sessions")
        < main.index("ensure_codex_cli")
    )
