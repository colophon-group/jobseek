from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
spec = importlib.util.spec_from_file_location("worker_bundle", REPO / "deploy/ai-filter-refresh/verify-bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

class RefreshWorkerTests(unittest.TestCase):
    def test_bundle_rejects_changed_code_dirty_source_wrong_revision_and_unpinned_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "worker.mjs").write_text("fixture")
            manifest = {"version":1,"sourceRevision":"a"*40,"sourceClean":True,
                "runtimeImage":"node:24-alpine@sha256:"+"b"*64,
                "workerSha256":hashlib.sha256(b"fixture").hexdigest()}
            def verify(changes):
                (root / "manifest.json").write_text(json.dumps({**manifest,**changes}))
                digest=hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
                return bundle.verify(root,"a"*40,digest)
            self.assertEqual(verify({})["sourceRevision"],"a"*40)
            for change in ({"sourceClean":False},{"sourceRevision":"c"*40},{"runtimeImage":"node:24-alpine"},{"workerSha256":"0"*64}):
                with self.subTest(change=change),self.assertRaises(ValueError): verify(change)
            verify({})
            with self.assertRaises(ValueError): bundle.verify(root,"a"*40,"0"*64)

    def test_launcher_is_unprivileged_bounded_and_never_mounts_the_docker_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root / "manifest.json").write_text(json.dumps({"runtimeImage":"node:24-alpine@sha256:"+"b"*64}))
            (root / "worker.mjs").write_text("fixture")
            (root / "worker-env").write_text("PRIVATE_FIXTURE_CREDENTIAL")
            fake=root / "docker"
            fake.write_text("#!/usr/bin/env python3\nimport json,os,sys\nopen(os.environ['WORKER_LAUNCH_ARGS'],'w').write(json.dumps(sys.argv[1:]))\n")
            fake.chmod(0o700)
            launcher=root / "run.sh"
            launcher.write_text((REPO / "deploy/ai-filter-refresh/run-host.sh").read_text().replace("/usr/bin/docker",str(fake)))
            environment={**os.environ,"JOBSEEK_NARROWED_BUNDLE":str(root),"CREDENTIALS_DIRECTORY":str(root),"WORKER_LAUNCH_ARGS":str(root / "args.json")}
            for args in ([],["--inspect","a"*36]):
                result=subprocess.run(["bash",str(launcher),*args],env=environment,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                command=json.loads((root / "args.json").read_text())
                joined=" ".join(command)
                for option in ("--read-only","--user","65532:65532","--cap-drop","--memory","--cpus","--pids-limit","--env-file"):
                    self.assertIn(option,command)
                self.assertNotIn("docker.sock",joined)
                self.assertNotIn("PRIVATE_FIXTURE_CREDENTIAL",joined+result.stdout+result.stderr)
                self.assertEqual("--name" in command,not bool(args))

    def test_host_pause_handles_active_failed_and_unloaded_service_without_starting_units(self):
        fake_systemctl = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ['PAUSE_FIXTURE_STATE'])
state = json.loads(path.read_text())
a = sys.argv[1:]
state['calls'].append(a)
code = 0
if a[0] == 'show' and a[1] == 'jobseek-ai-filter-refresh.timer':
    print('loaded')
elif a == ['disable', '--now', 'jobseek-ai-filter-refresh.timer']:
    state['enabled'] = False
    state['timer'] = 'inactive'
elif a == ['disable', 'jobseek-ai-filter-refresh.service']:
    state['service_enabled'] = False
elif a == ['stop', 'jobseek-ai-filter-refresh.service']:
    if state['service'] != 'failed': state['service'] = 'unloaded'
elif a == ['is-failed', '--quiet', 'jobseek-ai-filter-refresh.service']:
    code = 0 if state['service'] == 'failed' else 1
elif a == ['reset-failed', 'jobseek-ai-filter-refresh.service']:
    if state['service'] == 'unloaded': code = 1
    else: state['service'] = 'unloaded'
elif a == ['is-enabled', 'jobseek-ai-filter-refresh.timer']:
    print('enabled' if state['enabled'] else 'disabled')
    code = 0 if state['enabled'] else 1
elif a[0] == 'is-active' and a[1] in ['jobseek-ai-filter-refresh.timer', 'jobseek-ai-filter-refresh.service']:
    value = state['timer'] if a[1].endswith('.timer') else state['service']
    print('inactive' if value == 'unloaded' else value)
    code = 0 if value == 'active' else 3
else:
    code = 99
path.write_text(json.dumps(state))
sys.exit(code)
"""
        for initial in ('active', 'failed', 'unloaded'):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                binary = folder / 'systemctl'
                binary.write_text(fake_systemctl)
                binary.chmod(0o700)
                state_file = folder / 'state.json'
                state_file.write_text(json.dumps({'service': initial, 'timer': 'active', 'enabled': True, 'calls': []}))
                environment = {**os.environ, 'PATH': str(folder) + os.pathsep + os.environ['PATH'], 'PAUSE_FIXTURE_STATE': str(state_file)}
                for attempt in range(2):
                    result = subprocess.run(['bash', str(ROOT.parent / 'deploy/ai-filter-refresh/pause-host.sh')], env=environment, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('ai_filter_refresh_paused', result.stdout)
                state = json.loads(state_file.read_text())
                self.assertFalse(state['enabled'])
                self.assertEqual(state['timer'], 'inactive')
                self.assertEqual(state['service'], 'unloaded')
                resets = [call for call in state['calls'] if call[0] == 'reset-failed']
                self.assertEqual(len(resets), 1 if initial == 'failed' else 0)
                self.assertFalse(any(call[0] in ('start', 'restart', 'enable') for call in state['calls']))


if __name__ == "__main__": unittest.main()
