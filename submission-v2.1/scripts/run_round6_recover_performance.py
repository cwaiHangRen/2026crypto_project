#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, io, json, secrets, sys, time
from datetime import datetime
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
for import_root in (ROOT, ROOT/'src'):
    if str(import_root) not in sys.path: sys.path.insert(0,str(import_root))
from gm_provenance.service import ProvenanceService
from gm_provenance import media, manifest
from gm_provenance.development_eval import _image_transform
from gm_provenance.manifest import dumps

def crop(data,keep,offset=(0.,0.)):
 im=Image.open(io.BytesIO(data)).convert('RGB'); s=keep**.5; w,h=round(im.width*s),round(im.height*s); l=max(0,min(im.width-w,int((im.width-w)/2+offset[0]*im.width))); t=max(0,min(im.height-h,int((im.height-h)/2+offset[1]*im.height))); o=io.BytesIO(); im.crop((l,t,l+w,t+h)).save(o,'PNG'); return o.getvalue()
def quant(v,p): return round(sorted(v)[int(round((len(v)-1)*p))],3) if v else None
def timed_verify(svc,path,sidecar,mode,profile,snap,pub):
 spans={k:0. for k in ('signature_ms','hash_ms','parent_ms','state_ms','detector_ms','geometry_ms','dct_ms','ecc_ms')}; orig=(manifest.verify_manifest,svc.crypto.sm3,svc._check_parent,svc.verify_state_snapshot,media.detect)
 from gm_provenance import watermark, watermark_sync
 extra=[]
 for mod,name,key in ((watermark,'_dct_blocks','dct_ms'),(watermark_sync,'_geometry_candidates','geometry_ms'),(watermark_sync,'_decode_work_gray','ecc_ms')):
  fn=getattr(mod,name)
  def wrapped(*a,_fn=fn,_key=key,**kw):
   t=time.perf_counter(); value=_fn(*a,**kw)
   if _key == 'geometry_ms':
    def measured_iterator():
     try:
      yield from value
     finally:
      spans[_key]+=(time.perf_counter()-t)*1000
    return measured_iterator()
   spans[_key]+=(time.perf_counter()-t)*1000
   return value
  setattr(mod,name,wrapped); extra.append((mod,name,fn))
 def wrap(fn,key):
  def f(*a,**kw):
   t=time.perf_counter()
   try:return fn(*a,**kw)
   finally:spans[key]+=(time.perf_counter()-t)*1000
  return f
 manifest.verify_manifest=wrap(orig[0],'signature_ms'); svc.crypto.sm3=wrap(orig[1],'hash_ms'); svc._check_parent=wrap(orig[2],'parent_ms'); svc.verify_state_snapshot=wrap(orig[3],'state_ms'); media.detect=wrap(orig[4],'detector_ms'); t=time.perf_counter()
 try:r=svc.verify(path,sidecar,mode=mode,state_snapshot_path=snap,state_public_path=pub,decoder_profile=profile); err=None
 except Exception as e:r={'conclusion':'ERROR'}; err=f'{type(e).__name__}: {e}'
 spans['total_ms']=(time.perf_counter()-t)*1000; manifest.verify_manifest,svc.crypto.sm3,svc._check_parent,svc.verify_state_snapshot,media.detect=orig
 for mod,name,fn in extra: setattr(mod,name,fn)
 return r,spans,err
def run(repetitions=10,run_id=None):
 def provision_publisher(service, private_path, public_path, passphrase, publisher_id):
  service.keygen(private_path, public_path, passphrase); service.trust_key(public_path, publisher_id)
 rid=run_id or datetime.now().strftime('%Y%m%dT%H%M%S-round6-recover-perf'); out=ROOT/'results'/'round6-performance'/rid; out.mkdir(parents=True,exist_ok=False); work=out/'work'; work.mkdir()
 rows=[json.loads(x) for x in (ROOT/'data/round3-development-v3/dataset_manifest.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]; idx={x['sample_id']:x for x in rows}; src1=ROOT/idx['img-real-coco-139']['local_path']; src2=ROOT/idx['img-real-coco-285']['local_path']; pdfsrc=ROOT/idx['pdf-synth-001']['local_path']
 svc=ProvenanceService(work/'registry'); priv,pub=work/'publisher.pem',work/'publisher.pub'; ppass=secrets.token_urlsafe(32); provision_publisher(svc,priv,pub,ppass,'round6-perf'); r1=svc.register(src1,priv,ppass,publisher_id='round6-perf'); r2=svc.register(src2,priv,ppass,publisher_id='round6-perf'); rp=svc.register(pdfsrc,priv,ppass,publisher_id='round6-perf')
 mp=work/'manifest.json'; mp.write_bytes(dumps(r1['manifest'])); pmp=work/'pdf-manifest.json'; pmp.write_bytes(dumps(rp['manifest'])); spriv,spub,snap=work/'state.pem',work/'state.pub',work/'state-snapshot.json'; spass=secrets.token_urlsafe(32); svc.keygen(spriv,spub,spass); svc.state_snapshot(spriv,spass,snap,spub)
 a=Path(r1['media_path']).read_bytes(); b=Path(r2['media_path']).read_bytes(); ia=Image.open(io.BytesIO(a)).convert('RGB'); ib=Image.open(io.BytesIO(b)).convert('RGB'); mm=Image.new('RGB',(ia.width+ib.width,max(ia.height,ib.height)),(127,127,127)); mm.paste(ia,(0,0)); mm.paste(ib,(ia.width,0)); mo=io.BytesIO(); mm.save(mo,'PNG')
 cases=[('exact',a,'candidate',True,mp),('resize',_image_transform(a,'resize',.75),'candidate',False,None),('crop_center',crop(a,.90),'candidate',False,None),('crop_offset',crop(a,.95,(.02,0)),'candidate',False,None),('crop_combo',_image_transform(crop(a,.957,(.01,-.01)),'resize_jpeg',[.75,75]),'candidate',False,None),('negative',src1.read_bytes(),'negative',False,None),('mixed',mo.getvalue(),'negative',False,None),('pdf_exact',Path(rp['media_path']).read_bytes(),'candidate',True,pmp)]
 # Preflight is the only source of case labels; expected IDs are held outside detector.
 manifest_cases=[]
 for name,data,_,ret,side in cases:
  pre={p: media.detect(data, media.sniff(data), decoder_profile=p) if media.sniff(data).startswith(('image/','application/pdf')) else {'status':'UNSUPPORTED'} for p in ('dct-difference-v1','decoder-v2-sync')}
  expected=(r1['manifest']['body']['content_id'] if name!='pdf_exact' else rp['manifest']['body']['content_id'])
  sync_success=pre['decoder-v2-sync'].get('status')=='FOUND' and expected in pre['decoder-v2-sync'].get('content_ids',[])
  d0_found=pre['dct-difference-v1'].get('status')=='FOUND' and expected in pre['dct-difference-v1'].get('content_ids',[])
  label='sync_success' if sync_success else ('d0_success' if d0_found else ('oracle_gap' if pre['decoder-v2-sync'].get('status')=='FOUND' else 'sync_fail'))
  manifest_cases.append({'case':name,'expected_content_id':expected,'sidecar':'retained' if ret else 'lost','preflight':pre,'classification':label,'sha256':hashlib.sha256(data).hexdigest()})
 (out/'case_manifest.json').write_text(json.dumps({'schema_version':'round6-case-manifest-1','cases':manifest_cases},ensure_ascii=False,indent=2),encoding='utf-8')
 frozen=hashlib.sha256(json.dumps(svc.registry.status(),sort_keys=True).encode()).hexdigest(); rec=[]
 for name,data,cat,ret,side in cases:
  path=work/(name+'.bin'); path.write_bytes(data)
  for mode in ('recover','full'):
   for prof in ('dct-difference-v1','decoder-v2-sync'):
    for n in range(repetitions+1):
     result,timing,error=timed_verify(svc,path,side,mode,prof,snap,spub); rec.append({'case':name,'category':next(x['classification'] for x in manifest_cases if x['case']==name),'sidecar':'retained' if ret else 'lost','mode':mode,'decoder_profile':prof,'repeat':n,'warmup':n==0,'timing_ms':{k:round(v,3) for k,v in timing.items()},'conclusion':result.get('conclusion'),'watermark_status':result.get('watermark_status'),'recovered_content_ids':result.get('recovered_content_ids'),'content_hash_match':result.get('content_hash_match'),'signature_valid':result.get('signature_valid'),'parent_chain_status':result.get('parent_chain_status'),'state_freshness':result.get('state_freshness'),'search_complete':result.get('search_complete'),'search_stop_reason':result.get('search_stop_reason'),'error':error})
 (out/'trials.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rec),encoding='utf-8'); summaries=[]
 for name,_,cat,ret,_ in cases:
  cat=next(x['classification'] for x in manifest_cases if x['case']==name)
  for mode in ('recover','full'):
   for prof in ('dct-difference-v1','decoder-v2-sync'):
    rs=[x for x in rec if x['case']==name and x['mode']==mode and x['decoder_profile']==prof and not x['warmup']]; ts=[x['timing_ms']['total_ms'] for x in rs]; summaries.append({'case':name,'category':cat,'sidecar':'retained' if ret else 'lost','mode':mode,'decoder_profile':prof,'repetitions':len(rs),'p50_ms':quant(ts,.5),'p95_ms':quant(ts,.95),'max_ms':round(max(ts),3),'timing_p50_ms':{k:quant([x['timing_ms'][k] for x in rs],.5) for k in ('signature_ms','hash_ms','parent_ms','state_ms','detector_ms','geometry_ms','dct_ms','ecc_ms','total_ms')},'outcomes':{c:sum(x['conclusion']==c for x in rs) for c in sorted({x['conclusion'] for x in rs})}})
 final=hashlib.sha256(json.dumps(svc.registry.status(),sort_keys=True).encode()).hexdigest(); summary={'schema_version':'round6-recover-performance-2','status':'completed','run_id':rid,'repetitions_per_cell':repetitions,'warmups_per_cell':1,'fixed_case_count':8,'freeze_before_trials':frozen,'freeze_after_trials':final,'registry_unchanged':frozen==final,'state_key':'independent random key (secret omitted)','summary':summaries}; (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8'); return out
if __name__=='__main__':
 ap=argparse.ArgumentParser(); ap.add_argument('--repetitions',type=int,default=10); ap.add_argument('--run-id'); a=ap.parse_args(); print(run(a.repetitions,a.run_id))
