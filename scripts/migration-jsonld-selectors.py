from pathlib import Path
import os
import re
import sys

if len(sys.argv) != 4 or sys.argv[1] not in {'stage','clear'}:
    raise SystemExit('usage: stage|clear revision Kandou_detail_URL')
mode, revision, kandou_url = sys.argv[1:]
if re.fullmatch(r'[0-9a-f]{40}',revision) is None:
    raise SystemExit('invalid revision')
if re.fullmatch(r'https://kandou[.]bamboohr[.]com/careers/[1-9][0-9]{0,6}',kandou_url) is None:
    raise SystemExit('invalid Kandou detail URL')
root = Path('/home/deploy')
env_file = root / '.env'
snapshot_file = root / '.crawler-active-release/environment.env'
receipt = root / '.lightpanda-b0-active-v1'
if receipt.exists() or receipt.is_symlink():
    raise SystemExit('B0 must be cold before changing selectors')
snapshot = snapshot_file.read_text()
if f'JOBSEEK_DEPLOY_REVISION={revision}\n' not in snapshot:
    raise SystemExit('unexpected active crawler revision')
current = env_file.read_text()
selectors = {
    'JSONLD_CAPTURE_HOSTS': 'corporacaogpa.gupy.io,jobs.pwc.de,careers-gdbiw.icims.com,nttdata.jobs',
    'SMARTRECRUITERS_GO_BOARD_IDS': '4fd07c26-62bb-46af-a705-8533c074e28d,d87bd096-8b45-4b97-bf70-bcd3c10f62fc,868edd45-75dc-4174-adab-ffa2831957c1,0f2fad9a-c102-49c9-9233-21bc6785a74e',
    'SMARTRECRUITERS_GO_DETAIL_BOARD_IDS': '4fd07c26-62bb-46af-a705-8533c074e28d,d87bd096-8b45-4b97-bf70-bcd3c10f62fc,868edd45-75dc-4174-adab-ffa2831957c1,628cc519-cf15-43df-8ee0-0be4431326c6,143b8a70-441d-41c5-8001-0876d6e570e4',
    'WORKDAY_REPLAY_CAPTURE_BOARD_ID': '02ff01a5-d8ec-4696-8a45-17e3c8d6483c',
    'JOIN_CAPTURE_SLUGS': 'agentur-kuehnende,stellantisandyou,lebensmittel-knupferde',
    'RECRUITEE_CAPTURE_TENANTS': 'gorgias',
    'KANDOU_JSONLD_CAPTURE_URL': kandou_url,
    'WORKDAY_DETAIL_CAPTURE_HOST': 'freseniusglobal.wd3.myworkdayjobs.com',
    'PINPOINT_CAPTURE_TENANTS': 'accelercomm,includedhealth,penumbrainc,reply',
    'WORKDAY_GO_BOARD_IDS': '20eae165-5251-40d4-b9a0-0254f4bd1ab3',
    'WORKDAY_GO_PERCENT': '5',
    'RECRUITEE_GO_BOARD_IDS': '0f1c7be6-42f6-4706-952c-0d304f8011ba,d3df984c-5f08-47ff-a299-c54a700f5eee,e43481a7-561a-4f29-ab36-df8c5ddaca9f',
    'PINPOINT_GO_BOARD_IDS': '0ef903fe-82c9-4610-bdfc-0360f9d4226a,b2ce0b69-5896-4df3-8b12-5a6dce9a85d7,ef5d7575-4a45-44d7-8826-ce86a85085c2',
    'BOOKING_GO_BOARD_ID': 'd9730d93-3a2a-4003-a99e-4a8f0830aa2b',
    'TEAMTAILOR_RSS_GO_BOARD_IDS': '72d8d377-2242-4203-9270-c7447b4a3b56',
    'GREENHOUSE_GO_PERCENT': '100',
    'ASHBY_GO_PERCENT': '100',
    'SUCCESSFACTORS_RSS_GO_BOARD_IDS': '35461192-d877-4146-8f0d-31c97f14fedd',
    'PERSONIO_GO_BOARD_IDS': 'fdbe61f8-4615-49e6-8970-7e5e76452e24',
    'WORKABLE_CAPTURE_SLUGS': 'pix4d,debiopharm,unit8,hack-the-box-ltd',
    'WORKABLE_DETAIL_CAPTURE_JOBS': 'lucidya/4733C5B44F,lucidya/E258A7CDA9,unit8/2FA384D52A,unit8/ACABC868E8',
    'WORKABLE_GO_DETAIL_BOARD_IDS': 'aef95fd2-55ea-43cd-9aa6-9bd541c2bc4e',
    'WORKABLE_GO_PERCENT': '25',
    'WORKABLE_GO_BOARD_IDS': '06e7e8f2-4741-49b2-a135-96cfeb3bcfdd,631e7fce-366e-409d-b372-4ee4870e74c1,acecf5fd-3252-420d-a098-542cb1c0fc77,08c21917-9457-4b19-b0e6-40d0280a36c4',
    'SITEMAP_GO_BOARD_IDS': 'c4779214-ef92-4261-98fb-ae64f264fd23',
}
if mode == 'stage':
    without_compose = ''.join(line for line in current.splitlines(keepends=True) if not line.startswith('COMPOSE_FILE='))
    if without_compose != snapshot:
        raise SystemExit('host environment does not exactly match release snapshot')
    if any(any(line.startswith(f'{key}=') for line in current.splitlines()) for key in selectors):
        raise SystemExit('a selector is already staged')
    if not current.endswith('\n'):
        raise SystemExit('host environment lacks final newline')
    updated = current + ''.join(f'{key}={value}\n' for key,value in selectors.items())
else:
    found=set()
    remaining=[]
    for line in current.splitlines(keepends=True):
        key,sep,value=line.partition('=')
        if key in selectors:
            if not sep or value != selectors[key]+'\n' or key in found:
                raise SystemExit(f'unexpected or duplicate selector: {key}')
            found.add(key)
        else:
            remaining.append(line)
    if found != set(selectors):
        raise SystemExit(f'selector set differs: {sorted(found)}')
    updated = ''.join(remaining)
    without_compose = ''.join(line for line in updated.splitlines(keepends=True) if not line.startswith('COMPOSE_FILE='))
    if without_compose != snapshot:
        raise SystemExit('cleared environment does not exactly match release snapshot')
path = env_file.with_name(f'.env.{mode}-post-go-experience.tmp')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd,'w') as output:
        output.write(updated)
        output.flush()
        os.fsync(output.fileno())
    os.replace(path,env_file)
except BaseException:
    path.unlink(missing_ok=True)
    raise
print(f'{mode} completed for {len(selectors)} exact selectors; release snapshot verified')
