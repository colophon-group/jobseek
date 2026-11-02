#!/usr/bin/env python3
"""One incident, one complete prior-release restore; execute only in maintenance window."""
from __future__ import annotations
import argparse,datetime,hashlib,json,os,platform,pwd,re,shlex,shutil,signal,stat,subprocess,sys,tempfile,time,urllib.parse,urllib.request
from pathlib import Path,PurePosixPath
ROOT=Path('/home/deploy')
SOURCE='2a200c349ffdf0df3899e1ac38dea83e32031bbe'
FAILED='0cacb5d74341c2f93ed478c5cc6f99f9e8743827'
RUNTIME='ace0d2d14d2f72a1a8edea8fe5dddea1ac266aec804117f8b0a95cf26fdf1a7a'
IMAGE='sha256:c7e76a2d4abfeb8c8ab5962d566e2669b73e9dfe33fe497ad02299850fee2b3a'
DATA_SOURCE='909e86b156af479a0e93c5775a002811444b0c27'
DATA='f345cd4096083e7b1ee5880acf6a4a1ae289079a5b11d1255dbce1721154690b'
INPUT_HASH='c1bbaf1de9f12a275eb29a22fe6793bb350c5a52890ffc6485217aa75882166f'
PRECUTOVER_HASH='76f46e6c80f032f98c5d2221190323d9ba924f47b048b821580b3be4b069f7c1'
ARCHIVE_HASH='6bc2d026dd80f382df4cd1bb3da559224cb01fcecb6b37c9c352803a3e4cbe5e'
KEYS='cf779b72dfdc13892912baef1103914bb7aac59be2255c587dd8e50921abaa52'
UUIDS='14f8ff623ca996855464a4844f445ab0eef1229350d3c35a693d4810964275c3'
WRITERS=('ordinary-go','lease-reaper','exporter','drain')
SERVICES=('redis',*WRITERS,'alloy')
SPECS=('deploy.sh','deploy_helpers.sh','docker-compose.yml','lightpanda-b0-enabled.override.yml','alloy.river','scripts/native-deploy-tools.sh','scripts/crawler-deploy-tools-amd64','scripts/crawler-deploy-tools-arm64','scripts/crawler-deploy-tools.source','scripts/crawler-deploy-tools.sha256','scripts/lightpanda-claimant-credentials.py','scripts/lightpanda-b0-cutover.sh','scripts/ordinary-go-cutover.sh','scripts/verify-crawler-release-bridge.py')
PRESENCE='.jobseek-deploy-spec-presence-v1'
LATEST_START=datetime.datetime(2026,11,2,12,43,tzinfo=datetime.timezone.utc)
DEADLINE=datetime.datetime(2026,11,2,12,55,tzinfo=datetime.timezone.utc)
ORIGINAL_ENV_SHA='98e4910962f06d1183f19241878ae7fa5a9e8df5eb4be475134b677b01c6ec74'
ORIGINAL_GENERATION_NAME_SHA='101272a51f7c701ee5768c938c23a64dfb2e4447eb178472323840764a9f42e1'
ORIGINAL_MANIFEST_SHA='49a39d3c401a3fa6bdb683a1850de32c1f6b7191a56ad377a1a9fd7c68b4161f'
MONOTONIC_DEADLINE=time.monotonic()+450
BASE_ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','COMPOSE_PROJECT_NAME':'deploy'}
class Refusal(Exception):pass
def require(ok,reason):
 if not ok:raise Refusal(reason)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def regular(p):
 s=p.lstat();require(stat.S_ISREG(s.st_mode) and not stat.S_ISLNK(s.st_mode),'unsafe-file');return s

def values(p):
 regular(p);out={}
 for l in p.read_text().splitlines():
  if '=' not in l:continue
  k,v=l.split('=',1);require(k not in out,'duplicate-environment-key');out[k]=v
 return out

def run(args,timeout=30,env=None,input=None):
 require(datetime.datetime.now(datetime.timezone.utc)<DEADLINE,'incident-deadline-expired')
 remaining=min((DEADLINE-datetime.datetime.now(datetime.timezone.utc)).total_seconds(),MONOTONIC_DEADLINE-time.monotonic());require(remaining>0,'execution-budget-expired')
 p=subprocess.run(args,input=input,env=BASE_ENV if env is None else env,capture_output=True,timeout=min(timeout,remaining))
 require(p.returncode==0,'subcommand-refused');return p.stdout

def contain_writers(compose,known_ids):
 # Full-writer stop/kill has its own reserved120s; never start during cleanup.
 end=time.monotonic()+120
 def cleanup(args,timeout):
  left=end-time.monotonic();require(left>0,'writer-containment-budget')
  p=subprocess.run(args,env=BASE_ENV,capture_output=True,timeout=min(timeout,left));require(p.returncode==0,'writer-containment-command-failed');return p.stdout
 def listing():
  lines=cleanup(['docker','ps','--filter','label=com.docker.compose.project=deploy','--format','{{.ID}} {{.Label "com.docker.compose.service"}}'],10).decode().splitlines();require(len(lines)<=64,'failure-stop-bound');ids=[]
  for line in lines:
   parts=line.split();require(len(parts)==2 and re.fullmatch('[a-f0-9]{12,64}',parts[0]),'failure-stop-shape')
   if parts[1] in WRITERS:ids.append(parts[0])
  return ids
 try:
  ids=listing()
  if ids:cleanup(['docker','stop','--timeout','45',*ids],55)
 except BaseException:
  try:cleanup([*compose,'stop','--timeout','10',*WRITERS],20)
  except BaseException:
   try:cleanup(['docker','stop','--timeout','5',*known_ids],10)
   except BaseException:pass
 try:
  remaining=listing()
  if remaining:cleanup(['docker','kill',*remaining],10)
 except BaseException:
  cleanup([*compose,'kill',*WRITERS],15)
 require(not listing(),'writer-containment-unproved')

def expected_canonical(o):
 return o.get('count')==8288 and o.get('keys_SHA256')==KEYS and o.get('UUID_SHA256')==UUIDS and o.get('epoch')==404 and o.get('schema')=='0042' and o.get('active_plans')==0

def canonical(env):
 raw=shlex.split(env['LOCAL_DATABASE_URL']);require(len(raw)==1,'database-reference-shape');u=urllib.parse.urlsplit(raw[0]);require(u.scheme in ('postgres','postgresql'),'database-reference-scheme')
 pg=dict(BASE_ENV,PGHOST=u.hostname or '',PGPORT=str(u.port or 5432),PGUSER=urllib.parse.unquote(u.username or ''),PGPASSWORD=urllib.parse.unquote(u.password or ''),PGDATABASE=urllib.parse.unquote(u.path[1:]),PGCONNECT_TIMEOUT='8',PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=30000')
 for k,v in urllib.parse.parse_qsl(u.query):
  if k=='sslmode':pg['PGSSLMODE']=v
 sql="SELECT json_build_object('boards',(SELECT json_agg(json_build_array(c.slug,b.board_slug,b.id::text) ORDER BY c.slug,b.board_slug) FROM job_board b JOIN company c ON c.id=b.company_id WHERE b.is_enabled),'epoch',(SELECT last_value FROM lightpanda_b0_routing_epoch_seq),'schema',(SELECT version_num FROM alembic_version),'active_plans',(SELECT count(*) FROM ordinary_worker_ownership_plan WHERE state='active'));"
 o=json.loads(run(['psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1'],40,pg,sql.encode()));rows=o.pop('boards');require(isinstance(rows,list) and len(rows)<=20000,'canonical-bound')
 o.update(count=len(rows),keys_SHA256=hashlib.sha256(json.dumps(sorted([r[0],r[1]] for r in rows),ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),UUID_SHA256=hashlib.sha256(json.dumps(sorted(r[2] for r in rows),separators=(',',':')).encode()).hexdigest());require(expected_canonical(o),'canonical-precutover-state-differs');return o

def actor(service):
 ids=run(['docker','ps','-a','--filter','label=com.docker.compose.project=deploy','--filter','label=com.docker.compose.service='+service,'--format','{{.ID}}']).decode().split();require(len(ids)==1 and re.fullmatch('[a-f0-9]{12,64}',ids[0]),'actor-not-unique')
 o=json.loads(run(['docker','inspect','--format','{{json .}}',ids[0]]));return ids[0],o

def writer_guard(stopped):
 ids=[]
 for service in WRITERS:
  ident,o=actor(service);require(o['Config']['Image'].endswith('@'+IMAGE),'actor-image-differs');require(not o['State']['OOMKilled'],'actor-oom')
  require(o['State']['Running'] is (not stopped),'actor-running-state-differs');ids.append(ident)
 return ids

def parse_presence(p):
 lines=p.read_text().splitlines();require(lines and lines[0]=='DEPLOY_SPEC_PRESENCE_FORMAT_VERSION=1','presence-version');out={}
 for l in lines[1:]:
  v=l.split();require(len(v)==4,'presence-shape');state,sha,mode,name=v;require(name in SPECS and name not in out,'presence-name')
  require((state=='ABSENT' and sha==mode=='-') or (state=='PRESENT' and re.fullmatch('[a-f0-9]{64}',sha) and re.fullmatch('[0-7]{3,4}',mode)),'presence-value');out[name]=(state,sha,mode)
 require(set(out)==set(SPECS) and out['docker-compose.yml'][0]=='PRESENT','presence-completeness');return out

def verify_generation(generation,manifest,environment_override=None):
 for name,key in [('docker-compose.yml','COMPOSE_SHA256'),('environment.env','ENVIRONMENT_SHA256'),('success.env','SUCCESS_SHA256'),('data-files.sha256','DATA_FILES_SHA256')]:
  regular(generation/name);actual=digest(environment_override) if name=='environment.env' and environment_override is not None else digest(generation/name);require(actual==manifest[key],'generation-evidence-digest')
 require(manifest['DATA_FILES_SHA256']==DATA and manifest['DATA_CONTRACT_SHA256']==DATA,'generation-data-contract')
 data=generation/'data';require(data.is_dir() and not data.is_symlink(),'generation-data-root');names=set()
 for line in (generation/'data-files.sha256').read_text().splitlines():
  m=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._/-]+\.csv)',line);require(m is not None,'data-manifest-shape');relative=PurePosixPath(m[2]);require(not relative.is_absolute() and '..' not in relative.parts and str(relative) not in names,'data-manifest-path');target=data/str(relative);regular(target)
  require(data in target.resolve().parents and not any(part.is_symlink() for part in target.parents if part!=data and data in part.parents),'data-path-boundary');require(digest(target)==m[1],'data-file-digest');names.add(str(relative))
 require(len(names)==8 and names=={str(p.relative_to(data)) for p in data.rglob('*.csv')},'complete-data-tree')

def original_environment(generation,manifest):
 require(manifest['ENVIRONMENT_SHA256']==ORIGINAL_ENV_SHA,'original-environment-manifest-pin')
 base=ROOT/'.crawler-release-generations';directories=sorted(base.iterdir());require(len(directories)<=256,'original-generation-search-bound');matches=[]
 for directory in directories:
  if directory.is_dir() and not directory.is_symlink() and hashlib.sha256(directory.name.encode()).hexdigest()==ORIGINAL_GENERATION_NAME_SHA:matches.append(directory)
 require(len(matches)==1,'original-generation-not-unique');original=matches[0];require(original!=generation and original.parent==base,'original-generation-boundary')
 regular(original/'release.manifest');require(digest(original/'release.manifest')==ORIGINAL_MANIFEST_SHA,'original-generation-manifest-differs');own=values(original/'release.manifest');require(own['ENVIRONMENT_SHA256']==ORIGINAL_ENV_SHA,'original-own-environment-binding')
 env=original/'environment.env';info=regular(env);require(digest(env)==ORIGINAL_ENV_SHA and stat.S_IMODE(info.st_mode)==0o600,'original-environment-copy-differs');return env

def atomic_copy(source,target,mode):
 require(target.parent.is_dir() and not target.parent.is_symlink(),'unsafe-target-parent')
 if target.exists() or target.is_symlink():regular(target)
 fd,name=tempfile.mkstemp(prefix='.restore-native-1155.',dir=target.parent)
 try:
  owner=pwd.getpwnam('deploy')
  with os.fdopen(fd,'wb') as f:f.write(source.read_bytes());f.flush();os.fchmod(f.fileno(),mode);os.fchown(f.fileno(),owner.pw_uid,owner.pw_gid);os.fsync(f.fileno())
  os.replace(name,target)
 finally:
  if os.path.exists(name):os.unlink(name)
 require(digest(source)==digest(target) and stat.S_IMODE(target.stat().st_mode)==mode and target.stat().st_uid==owner.pw_uid and target.stat().st_gid==owner.pw_gid,'restore-copy-differs')

def main():
 def interrupted(signum,frame):raise Refusal('recovery-interrupted')
 signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
 ap=argparse.ArgumentParser();ap.add_argument('--reviewed-revision',required=True);ap.add_argument('--execute-reviewed-recovery',action='store_true');a=ap.parse_args()
 require(a.execute_reviewed_recovery and re.fullmatch('[a-f0-9]{40}',a.reviewed_revision),'explicit-reviewed-invocation-required')
 require(datetime.datetime.now(datetime.timezone.utc)<=LATEST_START,'latest-start-expired')
 require(os.geteuid()==0 and ROOT.resolve()==ROOT,'root-boundary')
 parent=(Path('/proc')/str(os.getppid())/'cmdline').read_bytes().split(b'\0');argv=[p.decode() for p in parent if p]
 require('/usr/local/sbin/jobseek-maintenance' in argv and 'window' in argv and '--operation' in argv and argv[argv.index('--operation')+1]=='restore-native-1155-v2' and '--revision' in argv and argv[argv.index('--revision')+1]==a.reviewed_revision and '--issue' in argv and argv[argv.index('--issue')+1]=='11489' and '--budget-seconds' in argv and argv[argv.index('--budget-seconds')+1]=='600','maintenance-wrapper-boundary')
 precutover=ROOT/'maintenance/restore-native-1155-v2.precutover.json';regular(precutover);require(digest(precutover)==PRECUTOVER_HASH,'precutover-proof-pin');proof=json.loads(precutover.read_text());require(proof['failed_source']==FAILED and proof['source_control_flow_verified'] is True and proof['MIGRATION_CUTOVER_REACHED'] is False and proof['FORWARD_SYNC_STARTED'] is False and proof['initial_failure_phase']=='inspect_default_missing_schedules','precutover-proof-refused')
 receipt=ROOT/'maintenance/restore-native-1155-v2.inputs.json';regular(receipt);require(digest(receipt)==INPUT_HASH,'input-receipt-pin');inputs=json.loads(receipt.read_text());require(expected_canonical(inputs['canonical']),'input-canonical-proof');observed=datetime.datetime.fromisoformat(inputs['observed_at_utc']);require(0<=(datetime.datetime.now(datetime.timezone.utc)-observed).total_seconds()<=1200,'input-proof-expired')
 require(set(inputs['inputs'])=={'.deploy-spec.rollback.tar',*[f'active/{x}' for x in ('environment.env','release.manifest','docker-compose.yml','success.env','data-files.sha256')],*[f'{base}/{x}' for base in ('incoming/scripts','scripts') for x in ('crawler-deploy-tools.source','crawler-deploy-tools.sha256','crawler-deploy-tools-arm64','crawler-deploy-tools-amd64')]},'input-pin-completeness')
 active=ROOT/'.crawler-active-release';require(active.is_symlink(),'active-pointer');generation=active.resolve(strict=True);require(generation.parent==ROOT/'.crawler-release-generations' and generation.is_dir() and not generation.is_symlink(),'generation-boundary')
 for name,pin in inputs['inputs'].items():
  require(name in {'.deploy-spec.rollback.tar',*[f'active/{x}' for x in ('environment.env','release.manifest','docker-compose.yml','success.env','data-files.sha256')],*[f'{base}/{x}' for base in ('incoming/scripts','scripts') for x in ('crawler-deploy-tools.source','crawler-deploy-tools.sha256','crawler-deploy-tools-arm64','crawler-deploy-tools-amd64')]},'input-path')
  p=generation/name.removeprefix('active/') if name.startswith('active/') else ROOT/name
  s=regular(p);require(digest(p)==pin['sha256'] and s.st_size==pin['bytes'] and stat.S_IMODE(s.st_mode)==pin['mode'],'input-pin-changed')
 archive=ROOT/'.deploy-spec.rollback.tar';require(digest(archive)==ARCHIVE_HASH and archive.stat().st_size==19518464,'original-archive-pin')
 tools=ROOT/'incoming/scripts';architecture={'aarch64':'arm64','arm64':'arm64','x86_64':'amd64'}.get(platform.machine());require(architecture is not None,'unsupported-architecture');tool=tools/('crawler-deploy-tools-'+architecture);require((tools/'crawler-deploy-tools.source').read_text().strip()==FAILED,'tool-source')
 require(json.loads(run([str(tool),'--build-info'],env={}))=={'source_revision':FAILED},'tool-compiled-source')
 run([str(tool),'release-bridge',str(generation),'colophon-group'],45,{})
 manifest=values(generation/'release.manifest');current=values(ROOT/'.env');original_env=original_environment(generation,manifest);old=values(original_env);verify_generation(generation,manifest,original_env)
 require(old['JOBSEEK_DEPLOY_REVISION']==SOURCE and old['JOBSEEK_RUNTIME_CONTRACT_SHA256']==RUNTIME and old['CRAWLER_IMAGE_REF']=='ghcr.io/colophon-group/jobseek-crawler@'+IMAGE and old['BROWSER_IMAGE_REF']=='ghcr.io/colophon-group/jobseek-crawler-browser@'+IMAGE,'old-runtime-binding')
 require(manifest['RELEASE_FORMAT_VERSION']=='3' and manifest['DATA_REVISION']==DATA_SOURCE and manifest['DATA_CONTRACT_SHA256']==DATA,'old-data-binding');require(current['JOBSEEK_DEPLOY_REVISION']==FAILED,'split-root-incident-shape');require(not (ROOT/'.env.rollback').exists(),'rollback-env-reappeared')
 prestart_ids=writer_guard(True);canonical(old)
 backup=ROOT/'.recovery1155-failed1156-v2';require(not backup.exists() and not backup.is_symlink(),'recovery-already-attempted')
 with tempfile.TemporaryDirectory(prefix='.recovery1155-verify.',dir=ROOT) as temp:
  extracted=Path(temp);run([str(tool),'spec-unpack',str(archive),str(extracted),PRESENCE,*SPECS],45,{})
  presence=parse_presence(extracted/PRESENCE);require(digest(extracted/'docker-compose.yml')==digest(generation/'docker-compose.yml'),'archive-active-compose-differs')
  compose=['docker','compose','--project-name','deploy','--env-file',str(original_env),'-f',str(extracted/'docker-compose.yml')]
  config=json.loads(run([*compose,'config','--format','json'],30));require(all(service in config['services'] for service in SERVICES),'full-service-contract')
  for service in WRITERS:require(config['services'][service]['image'].endswith('@'+IMAGE),'rendered-image-differs')
  backup.mkdir(mode=0o700)
  # Preserve corrupt generation evidence and the original archive before ANY repair.
  for source,name in [(generation/'environment.env','corrupted-active-environment.env'),(archive,'original-rollback-archive.tar'),(original_env,'verified-original-environment.env')]:
   regular(source);target=backup/name;shutil.copyfile(source,target);target.chmod(0o600);require(digest(source)==digest(target),'pre-repair-backup-differs')
   with target.open('rb') as f:os.fsync(f.fileno())
  require(digest(backup/'original-rollback-archive.tar')==ARCHIVE_HASH and digest(backup/'verified-original-environment.env')==ORIGINAL_ENV_SHA,'pre-repair-custody-differs')
  for name in ('.deploy-spec.rollback.tar','.env',*SPECS):
   p=ROOT/name
   if p.exists():regular(p);target=backup/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);shutil.copyfile(p,target);target.chmod(stat.S_IMODE(p.stat().st_mode));require(digest(p)==digest(target),'backup-differs')
  require(digest(backup/'.deploy-spec.rollback.tar')==ARCHIVE_HASH,'archive-backup-differs')
  for p in backup.rglob('*'):
   if p.is_file():
    with p.open('rb') as f:os.fsync(f.fileno())
  directory_fd=os.open(backup,os.O_RDONLY)
  try:os.fsync(directory_fd)
  finally:os.close(directory_fd)
  started=False
  try:
   # Restore only byte-identical manifest-authorized environment, never relax its expected hash.
   atomic_copy(original_env,generation/'environment.env',0o600);verify_generation(generation,manifest)
   for name,(state,sha,mode) in presence.items():
    if state=='PRESENT':
     target=ROOT/name;target.parent.mkdir(mode=0o755,parents=True,exist_ok=True);atomic_copy(extracted/name,target,int(mode,8));require(digest(target)==sha,'restored-spec-digest')
   atomic_copy(generation/'environment.env',ROOT/'.env',0o600);atomic_copy(generation/'environment.env',ROOT/'.env.rollback',0o600)
   compose=['docker','compose','--project-name','deploy','--env-file',str(ROOT/'.env'),'-f',str(ROOT/'docker-compose.yml')]
   started=True
   run([*compose,'up','-d','--no-build','--pull','never',*SERVICES],90)
   until=time.monotonic()+150
   while True:
    ready=True
    for service in SERVICES:
     _,o=actor(service);state=o['State'];ready=ready and state['Running'] and not state['OOMKilled'] and state.get('Health',{}).get('Status','none') in ('none','healthy')
    if ready:
     try:
      with urllib.request.urlopen('http://127.0.0.1:9104/healthz',timeout=3) as r:ready=r.status==204 and r.headers.get('Jobseek-Ordinary-Source')==SOURCE and r.headers.get('Jobseek-Ordinary-Mode')=='default'
      with urllib.request.urlopen('http://127.0.0.1:12346/-/ready',timeout=3) as r:ready=ready and r.status==200
     except Exception:ready=False
    if ready:break
    require(time.monotonic()<until,'full-readiness-deadline');time.sleep(2)
   writer_guard(False);verify_generation(generation,manifest);require((ROOT/'.env').read_bytes()==(generation/'environment.env').read_bytes(),'root-active-env-differs');require(active.resolve()==generation,'active-generation-changed');run([str(tool),'release-bridge',str(generation),'colophon-group'],45,{})
   for name,(state,sha,mode) in presence.items():
    target=ROOT/name
    if state=='ABSENT' and (target.exists() or target.is_symlink()):regular(target);target.unlink()
    if state=='PRESENT':require(digest(target)==sha and stat.S_IMODE(target.stat().st_mode)==int(mode,8),'final-spec-differs')
   result=canonical(old);require(digest(archive)==ARCHIVE_HASH and digest(backup/'.deploy-spec.rollback.tar')==ARCHIVE_HASH,'recovery-evidence-not-retained')
   print(json.dumps({'state':'COMPLETE_PRIOR_NATIVE_RELEASE_RESTORED','source':SOURCE,'runtime_contract':RUNTIME,'image':IMAGE,'data_source':DATA_SOURCE,'data_contract':DATA,'all_four_native_actors_healthy':True,'all_six_services_ready':True,'canonical':result,'queue_epoch_schedule_writes':0,'sync_or_migrations':0,'archive_preserved':True,'corrupted_active_environment_preserved':True,'exact_original_environment_restored':True,'reviewed_revision':a.reviewed_revision}))
  except BaseException:
   # Preserve a fail-closed complete writer boundary, never restart a subset.
   if started:
    contain_writers(compose,prestart_ids)
   raise
if __name__=='__main__':
 try:main()
 except BaseException as e:
  print(json.dumps({'state':'REFUSED_OR_RESTORATION_FAILED','category':type(e).__name__,'reason':str(e) if isinstance(e,Refusal) else 'bounded-internal-failure'}));sys.exit(1)
