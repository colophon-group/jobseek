from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "apps/crawler/scripts/ordinary-go-cutover.sh"
SOURCE = "a" * 40
PLAN = "b" * 64
PROJECTION = "c" * 40
IMAGE = "ghcr.io/example/jobseek-crawler@sha256:" + "d" * 64
BROWSER = "ghcr.io/example/jobseek-crawler-browser@sha256:" + "e" * 64
BASH = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"


@pytest.fixture
def host(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    compose_hash = hashlib.sha256(b"reviewed-compose\n").hexdigest()
    (deploy / ".env").write_text(
        f"JOBSEEK_DEPLOY_REVISION={SOURCE}\nCRAWLER_IMAGE_REF={IMAGE}\n"
        f"BROWSER_IMAGE_REF={BROWSER}\nLIGHTPANDA_B0_SERVICE_HOST=10.0.0.5\n"
    )
    receipt = deploy / ".lightpanda-b0-active-v1"
    receipt.write_text(
        "schema=jobseek.lightpanda-b0-active/v1\nstate=active\ncohort=cdom\n"
        "namespace=production-b0\nshard_id=lightpanda-b0\nrouting_epoch=147\n"
        f"plan_digest={PLAN}\ncompose_digest={compose_hash}\n"
        f"crawler_image_ref={IMAGE}\ndeploy_revision={SOURCE}\nactivated_at_epoch=1\n"
    )
    receipt.chmod(0o600)
    # Exercise the actual supported shell driver with a bounded daemon substitute.
    # This proves order and containment, not installed-image or host admission.
    substitutes = r"""
id() { if [[ "$*" == -un ]]; then echo deploy; else command id "$@"; fi; }
flock() { return 0; }
stat() { printf '%s:600:1\n' "$(command id -u)"; }
sync() { return 0; }
sha256sum() {
  if type -P sha256sum >/dev/null; then command sha256sum "$@";
  else shasum -a 256 "$@"; fi
}
timeout() { shift 4; "$@"; }
sleep() { return 0; }
curl() {
  printf 'health:%s\n' "$*" >>"$TEST_LOG"
  [[ "$TEST_FAILURE" != health ]]
}
docker() {
  printf 'docker:%s\n' "$*" >>"$TEST_LOG"
  local args="$*" service= index= image=
  if [[ "$1" == compose ]]; then
    case "$args" in
      *' config -q') return 0 ;;
      *' config') printf 'reviewed-compose\n'; return 0 ;;
      *' --identity')
        printf '{"source_revision":"%s","profile":"greenhouse.token-skip/v1"}\n' \
          "$JOBSEEK_DEPLOY_REVISION"; return 0 ;;
      *' --stage-ownership') printf '{"state":"staged"}\n'; return 0 ;;
      *' --activate-first-ownership'|*' --retire-first-ownership')
        [[ -f "$RECEIPT" && -f "$REQUEST" && -f "$DEPLOY_DIR/stopped" ]] || return 93
        [[ "$TEST_FAILURE" != signal ]] || kill -TERM "$$"
        [[ "$TEST_FAILURE" != admin ]] || return 91
        printf 'native-effect\n' >>"$TEST_LOG"; return 0 ;;
      *' stop --timeout 60 '*) touch "$DEPLOY_DIR/stopped"; return 0 ;;
      *' kill '*) return 0 ;;
      *' up -d --force-recreate '*)
        touch "$DEPLOY_DIR/started"
        rm -f "$DEPLOY_DIR/stopped"
        return 0 ;;
      *' ps -aq '*|*' ps -q '*)
        service=${!#}
        if [[ "$service" == ordinary-go && ! -f "$DEPLOY_DIR/started" ]]; then return 0; fi
        case "$service" in
          worker-1) index=1 ;; worker-2) index=2 ;; worker-3) index=3 ;;
          browser-1) index=4 ;; exporter) index=5 ;; drain) index=6 ;;
          lightpanda-producer) index=7 ;; lightpanda-executor) index=8 ;;
          lightpanda-claimant) index=9 ;; ordinary-go) index=10 ;;
          *) return 94 ;;
        esac
        printf '%064d\n' "$index"; return 0 ;;
      *) return 95 ;;
    esac
  elif [[ "$1" == ps ]]; then return 0
  elif [[ "$1" == update ]]; then
    if [[ "$args" == *unless-stopped* && "$TEST_FAILURE" == arm ]]; then return 92; fi
    return 0
  elif [[ "$1" == inspect ]]; then
    index=$((10#${!#}))
    image=$CRAWLER_IMAGE_REF
    [[ "$index" != 4 ]] || image=$BROWSER_IMAGE_REF
    if [[ "$TEST_FAILURE" == image && "$index" == 1 ]]; then
      image="ghcr.io/example/jobseek-crawler@sha256:$(printf '%064d' 99)"
    fi
    case "$2:$3" in
      '-f:{{.Config.Image}}') printf '%s\n' "$image" ;;
      *RestartPolicy*Image*) printf 'false:no:%s\n' "$image" ;;
      *RestartPolicy*) printf 'true:unless-stopped\n' ;;
      *) printf 'healthy\n' ;;
    esac
    return 0
  fi
  return 96
}
"""
    script = WRAPPER.read_text()
    script = script.replace("set -euo pipefail", "set -euo pipefail\n" + substitutes, 1)
    script = script.replace("DEPLOY_DIR=/home/deploy", f'DEPLOY_DIR="{deploy}"', 1)
    script = script.replace(
        "LOCK=/run/lock/jobseek-crawler-mutation.lock", f'LOCK="{tmp_path / "mutation.lock"}"', 1
    )
    path = tmp_path / "ordinary-go-cutover.sh"
    path.write_text(script)
    env = {
        "PATH": os.environ["PATH"],
        "TEST_LOG": str(tmp_path / "commands.log"),
        "TEST_FAILURE": "",
    }
    return path, deploy, env


def invoke(host: tuple[Path, Path, dict[str, str]], *args: str) -> subprocess.CompletedProcess[str]:
    path, _, env = host
    return subprocess.run([BASH, str(path), *args], env=env, capture_output=True, text=True)


def test_complete_first_owner_cutover_and_retirement(
    host: tuple[Path, Path, dict[str, str]],
) -> None:
    _, deploy, env = host
    b0 = (deploy / ".lightpanda-b0-active-v1").read_bytes()
    result = invoke(host, "activate", PLAN, PROJECTION)
    assert result.returncode == 0, result.stderr
    receipt = deploy / ".ordinary-go-owner-v1"
    assert "state=active\n" in receipt.read_text()
    assert receipt.stat().st_mode & 0o777 == 0o600
    events = Path(env["TEST_LOG"]).read_text().splitlines()
    effect = events.index("native-effect")
    stopped = next(i for i, event in enumerate(events) if " stop --timeout 60 " in event)
    startup = next(i for i, event in enumerate(events) if " up -d --force-recreate " in event)
    arm = next(i for i, event in enumerate(events) if "update --restart unless-stopped" in event)
    assert stopped < effect < startup < arm
    assert any("9104/healthz" in event for event in events[startup:arm])
    assert sum("update --restart no" in event for event in events[:effect]) == 9
    assert invoke(host, "retire").returncode == 0
    assert not receipt.exists()
    assert (deploy / ".lightpanda-b0-active-v1").read_bytes() == b0


@pytest.mark.parametrize("failure", ["image", "admin", "health", "arm", "signal"])
def test_failed_cutover_retains_identity_and_disables_restarts(
    host: tuple[Path, Path, dict[str, str]], failure: str
) -> None:
    _, deploy, env = host
    env["TEST_FAILURE"] = failure
    result = invoke(host, "activate", PLAN, PROJECTION)
    assert result.returncode != 0
    receipt = (deploy / ".ordinary-go-owner-v1").read_text()
    assert f"plan_sha256={PLAN}\n" in receipt
    events = Path(env["TEST_LOG"]).read_text().splitlines()
    if failure == "image":
        assert "native-effect" not in events
        assert not (deploy / ".ordinary-go-request-v1.json").exists()
    assert any(" kill worker-1 worker-2 worker-3" in event for event in events)
    last_stop = max(i for i, event in enumerate(events) if " stop --timeout 60 " in event)
    prior_updates = [event for event in events[:last_stop] if "update --restart no" in event]
    assert len(prior_updates) >= 18
    env["TEST_FAILURE"] = ""
    result = invoke(host, "recover-pending" if failure != "arm" else "retire")
    assert result.returncode == 0, result.stderr
    assert not (deploy / ".ordinary-go-owner-v1").exists()


def test_csv_publication_refuses_pending_owner_before_any_external_effect(tmp_path: Path) -> None:
    (tmp_path / ".ordinary-go-owner-v1").touch()
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/crawler-csv-sync-host.sh"), "--recover-only"],
        env={"PATH": os.environ["PATH"], "JOBSEEK_DEPLOY_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "retire native ordinary ownership" in result.stderr
