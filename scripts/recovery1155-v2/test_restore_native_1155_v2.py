from __future__ import annotations
import datetime,hashlib,importlib.util,json,os,stat,subprocess,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('rescue',Path(__file__).with_name('restore-native-1155-v2.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RecoveryGuards(unittest.TestCase):
 def test_expected_canonical_all_pins(self):
  good=dict(count=8288,keys_SHA256=m.KEYS,UUID_SHA256=m.UUIDS,epoch=404,schema='0042',active_plans=0);self.assertTrue(m.expected_canonical(good))
  for key in good:
   bad=good.copy();bad[key]='different'
   with self.subTest(key=key):self.assertFalse(m.expected_canonical(bad))
 def test_presence_contract(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'presence';valid='DEPLOY_SPEC_PRESENCE_FORMAT_VERSION=1\n'+'\n'.join('PRESENT '+'a'*64+' 644 '+name for name in m.SPECS)+'\n';p.write_text(valid);self.assertEqual(set(m.parse_presence(p)),set(m.SPECS))
   for bad in [valid.replace('FORMAT_VERSION=1','FORMAT_VERSION=2'),valid+'ABSENT - - ../escape\n',valid+'ABSENT - - deploy.sh\n',valid.replace('PRESENT '+('a'*64)+' 644 docker-compose.yml','ABSENT - - docker-compose.yml'),valid.replace(' 644 ',' 999 '),valid.replace(' deploy.sh\n',' unknown\n')]:
    p.write_text(bad)
    with self.subTest(case=bad[:30]),self.assertRaises(m.Refusal):m.parse_presence(p)
 def make_generation(self,root):
  (root/'data').mkdir()
  for i in range(8):(root/'data'/f'{i}.csv').write_text(f'key,value\n{i},fixture\n')
  manifest_body=''.join(m.digest(p)+'  '+p.name+'\n' for p in sorted((root/'data').glob('*.csv')));(root/'data-files.sha256').write_text(manifest_body)
  for name in ['docker-compose.yml','environment.env','success.env']:(root/name).write_text('fixture-'+name)
  values={key:m.digest(root/name) for name,key in [('docker-compose.yml','COMPOSE_SHA256'),('environment.env','ENVIRONMENT_SHA256'),('success.env','SUCCESS_SHA256'),('data-files.sha256','DATA_FILES_SHA256')]};values['DATA_CONTRACT_SHA256']=values['DATA_FILES_SHA256'];return values
 def test_complete_generation_and_corruption(self):
  cases=['valid','csv-content','extra-csv','missing-csv','symlink-csv','environment-content','duplicate-csv','data-contract']
  for case in cases:
   with self.subTest(case=case),tempfile.TemporaryDirectory() as t:
    root=Path(t).resolve();v=self.make_generation(root);expected=v['DATA_CONTRACT_SHA256']
    if case=='csv-content':(root/'data/0.csv').write_text('corrupt')
    if case=='extra-csv':(root/'data/extra.csv').write_text('extra')
    if case=='missing-csv':(root/'data/0.csv').unlink()
    if case=='symlink-csv':
     original=root/'data/0.csv';original.unlink();original.symlink_to(root/'data/1.csv')
    if case=='environment-content':(root/'environment.env').write_text('corrupt')
    if case=='duplicate-csv':
     p=root/'data-files.sha256';p.write_text(p.read_text()+p.read_text().splitlines()[0]+'\n');v['DATA_FILES_SHA256']=m.digest(p);v['DATA_CONTRACT_SHA256']=v['DATA_FILES_SHA256'];expected=v['DATA_FILES_SHA256']
    if case=='data-contract':v['DATA_CONTRACT_SHA256']='bad'
    with patch.object(m,'DATA',expected):
     if case=='valid':m.verify_generation(root,v)
     else:
      with self.assertRaises((m.Refusal,FileNotFoundError)):m.verify_generation(root,v)
 def test_atomic_copy_owner_mode_and_symlink_rejection(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t).resolve();source=root/'source';source.write_text('private-fixture');target=root/'target';owner=types.SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
   with patch.object(m.pwd,'getpwnam',return_value=owner):m.atomic_copy(source,target,0o600)
   self.assertEqual(target.read_bytes(),source.read_bytes());self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600);self.assertEqual(target.stat().st_uid,os.getuid());target.unlink();target.symlink_to(source)
   with self.assertRaises(m.Refusal):m.atomic_copy(source,target,0o600)
 def containment_case(self,discovery_fail=False,stop_fail=False):
  running={s:('%012x'%(i+1)) for i,s in enumerate(m.SERVICES)};calls=[];first=True
  def fake(args,**kwargs):
   nonlocal first
   calls.append(list(args))
   if args[:2]==['docker','ps']:
    if discovery_fail and first:first=False;raise subprocess.TimeoutExpired(args,10)
    text=''.join(ident+' '+service+'\n' for service,ident in running.items());return types.SimpleNamespace(returncode=0,stdout=text.encode())
   if 'stop' in args or 'kill' in args:
    if stop_fail and args[:2]==['docker','stop']:return types.SimpleNamespace(returncode=1,stdout=b'')
    # Every cleanup action addresses the complete known writer set, never Redis/Alloy.
    if args[:2]==['docker','stop'] or args[:2]==['docker','kill']:
     ids=args[4:] if args[1]=='stop' else args[2:]
     self.assertTrue(set(ids).issubset({running.get(s) for s in m.WRITERS}));self.assertFalse(running['redis'] in ids or running['alloy'] in ids)
     for service in list(running):
      if service in m.WRITERS and running[service] in ids:running.pop(service)
    else:
     self.assertEqual(tuple(args[-4:]),m.WRITERS)
     for service in m.WRITERS:running.pop(service,None)
    return types.SimpleNamespace(returncode=0,stdout=b'')
   self.fail('unexpected command')
  compose=['docker','compose','--project-name','deploy','--env-file','fixture','-f','fixture']
  with patch.object(m.subprocess,'run',side_effect=fake),patch.object(m,'DEADLINE',datetime.datetime(2000,1,1,tzinfo=datetime.timezone.utc)):m.contain_writers(compose,['a'*12]*4)
  self.assertEqual(set(running),{'redis','alloy'});self.assertFalse(any('up' in c or 'start' in c for c in calls))
 def test_containment_past_deadline(self):self.containment_case()
 def test_containment_discovery_failure_uses_full_compose_stop(self):self.containment_case(discovery_fail=True)
 def test_containment_stop_failure_uses_full_compose_stop(self):self.containment_case(stop_fail=True)
 def test_expired_normal_run_never_spawns(self):
  with patch.object(m,'DEADLINE',datetime.datetime(2000,1,1,tzinfo=datetime.timezone.utc)),patch.object(m.subprocess,'run') as spawn:
   with self.assertRaises(m.Refusal):m.run(['not-executed'])
   spawn.assert_not_called()
 def test_wrapper_required_before_receipt_read_or_command(self):
  with tempfile.TemporaryDirectory() as t,patch.object(m,'ROOT',Path(t).resolve()),patch.object(m.os,'geteuid',return_value=0),patch.object(Path,'read_bytes',return_value=b'python\x00unrelated.py\x00'),patch.object(sys,'argv',['restore','--reviewed-revision','a'*40,'--execute-reviewed-recovery']),patch.object(m.subprocess,'run') as spawn:
   with self.assertRaisesRegex(m.Refusal,'maintenance-wrapper-boundary'):m.main()
   spawn.assert_not_called()
 def test_exact_original_environment_copy_and_manifest_guards(self):
  for case in ['valid','wrong-target-hash','wrong-own-manifest','wrong-env-content','unsafe-mode','symlink-env']:
   with self.subTest(case=case),tempfile.TemporaryDirectory() as t:
    root=Path(t).resolve();base=root/'.crawler-release-generations';base.mkdir();active=base/'active';active.mkdir();original=base/'original';original.mkdir();env=original/'environment.env';env.write_text('EXACT=original\n');env.chmod(0o600);expected=m.digest(env);own=original/'release.manifest';own.write_text('ENVIRONMENT_SHA256='+expected+'\n');own_hash=m.digest(own)
    target={'ENVIRONMENT_SHA256':expected}
    if case=='wrong-target-hash':target['ENVIRONMENT_SHA256']='b'*64
    if case=='wrong-own-manifest':own.write_text('modified')
    if case=='wrong-env-content':env.write_text('modified')
    if case=='unsafe-mode':env.chmod(0o644)
    if case=='symlink-env':
     copy=original/'elsewhere.env';copy.write_bytes(env.read_bytes());env.unlink();env.symlink_to(copy)
    with patch.object(m,'ROOT',root),patch.object(m,'ORIGINAL_ENV_SHA',expected),patch.object(m,'ORIGINAL_GENERATION_NAME_SHA',hashlib.sha256(original.name.encode()).hexdigest()),patch.object(m,'ORIGINAL_MANIFEST_SHA',own_hash):
     if case=='valid':self.assertEqual(m.original_environment(active,target),env)
     else:
      with self.assertRaises(m.Refusal):m.original_environment(active,target)
 def test_corrupt_active_env_only_exact_override_then_full_repair(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t).resolve();generation=root/'generation';generation.mkdir();v=self.make_generation(generation);source=root/'original.env';source.write_bytes((generation/'environment.env').read_bytes());(generation/'environment.env').write_text('corrupted');owner=types.SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
   with patch.object(m,'DATA',v['DATA_CONTRACT_SHA256']):
    with self.assertRaises(m.Refusal):m.verify_generation(generation,v)
    m.verify_generation(generation,v,source);self.assertEqual((generation/'environment.env').read_bytes(),b'corrupted')
    with patch.object(m.pwd,'getpwnam',return_value=owner):m.atomic_copy(source,generation/'environment.env',0o600)
    m.verify_generation(generation,v)
    source.write_text('incorrect-copy')
    with self.assertRaises(m.Refusal):m.verify_generation(generation,v,source)
 def test_v2_latest_start_refuses_before_any_command(self):
  with patch.object(m,'LATEST_START',datetime.datetime(2000,1,1,tzinfo=datetime.timezone.utc)),patch.object(sys,'argv',['restore','--reviewed-revision','a'*40,'--execute-reviewed-recovery']),patch.object(m.subprocess,'run') as spawn:
   with self.assertRaisesRegex(m.Refusal,'latest-start-expired'):m.main()
   spawn.assert_not_called()
if __name__=='__main__':unittest.main()
