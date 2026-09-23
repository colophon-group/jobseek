import json,time,urllib.request,sys
from pathlib import Path
out=[]
for path in ['/en/company/aircall','/fr/company/hellofresh']:
 for i in range(6):
  t=time.perf_counter()
  with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:43189'+path,headers={'Accept':'text/html','User-Agent':'JobseekLocalCpuAudit/1.0'}),timeout=60) as r:
   body=r.read()
   out.append({'path':path,'attempt':i+1,'status':r.status,'bytes':len(body),'wall_ms':round(1000*(time.perf_counter()-t),3),'headers':{k:v for k,v in r.headers.items() if k.lower() in ['cache-control','x-nextjs-cache','x-nextjs-postponed','x-nextjs-prerender','content-type']},'has_company': ('Aircall' if 'aircall' in path else 'HelloFresh').encode() in body})
  time.sleep(1)
Path(sys.argv[1]).write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
