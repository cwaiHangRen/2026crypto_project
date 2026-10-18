from __future__ import annotations
import csv,html,io,json,secrets,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from . import media
class ScreenshotTaskManager:
 def __init__(self,root,verifier,max_workers=2): self.root=Path(root)/'screenshot-tasks';self.root.mkdir(parents=True,exist_ok=True);self.verifier=verifier;self._service=None;self._tasks={};self._lock=threading.RLock();self._executor=ThreadPoolExecutor(max_workers=max_workers)
 def submit(self,data,filename,source_kind,mode='recover'):
  if source_kind not in {'upload','paste','screen'}: raise ValueError('source_kind 必须是 upload、paste 或 screen')
  if len(data)>media.MAX_BYTES: raise ValueError('文件过大')
  if media.sniff(data) not in {'image/png','image/jpeg'}: raise ValueError('截图仅支持 PNG 或 JPEG')
  tid=secrets.token_urlsafe(18); name=Path(filename or 'capture.png').name[:120]; path=self.root/(tid+'-'+name);path.write_bytes(data); t={'task_id':tid,'source_kind':source_kind,'filename':name,'status':'queued','created_at':time.time(),'started_at':None,'finished_at':None,'result':None,'error':None,'_path':path,'_cancel':False}
  with self._lock:self._tasks[tid]=t
  self._executor.submit(self._run,tid,mode);return self._pub(t)
 def _pub(self,t): return {k:v for k,v in t.items() if not k.startswith('_')}
 def _run(self,tid,mode):
  with self._lock:
   t=self._tasks.get(tid)
   if not t or t['_cancel']: return
   t['status']='running';t['started_at']=time.time()
  try:
   result=self.verifier(t['_path'],None,mode)
   with self._lock:t['status']='cancelled' if t['_cancel'] else 'completed';t['result']=None if t['_cancel'] else result
  except Exception as e:
   with self._lock:t['status']='failed';t['error']=f'{type(e).__name__}: {e}'
  finally:
   with self._lock:t['finished_at']=time.time()
 def get(self,tid):
  with self._lock:
   t=self._tasks.get(tid)
   if not t or t['status']=='deleted':raise KeyError(tid)
   return self._pub(t)
 def list(self):
  with self._lock:return [self._pub(t) for t in self._tasks.values() if t['status']!='deleted']
 def cancel(self,tid):
  with self._lock:
   t=self._tasks.get(tid)
   if not t:raise KeyError(tid)
   if t['status'] in ('queued','running'):t['_cancel']=True;t['status']='cancelled'
   return self._pub(t)
 def delete(self,tid):
  with self._lock:
   t=self._tasks.get(tid)
   if not t:raise KeyError(tid)
   if t['status'] in ('queued','running'):raise ValueError('运行中的任务必须先取消')
   t['status']='deleted'
   try:t['_path'].unlink()
   except FileNotFoundError:pass
   return self._pub(t)
 def report(self,ids=None):
  rows=self.list(); wanted=set(ids or []);return [r for r in rows if not wanted or r['task_id'] in wanted]
 def close(self):self._executor.shutdown(wait=False,cancel_futures=True)
def report_json(rows):return json.dumps({'count':len(rows),'tasks':rows},ensure_ascii=False,indent=2)
def report_csv(rows):
 o=io.StringIO();f=['task_id','source_kind','filename','status','created_at','started_at','finished_at','error'];w=csv.DictWriter(o,fieldnames=f);w.writeheader();[w.writerow({k:r.get(k) for k in f}) for r in rows];return o.getvalue()
def report_html(rows):
 c=''.join('<tr>'+''.join(f'<td>{html.escape(str(r.get(k,"")))}</td>' for k in ('task_id','source_kind','filename','status','error'))+'</tr>' for r in rows);return '<!doctype html><meta charset="utf-8"><table><tr><th>任务</th><th>来源</th><th>文件</th><th>状态</th><th>错误</th></tr>'+c+'</table>'
