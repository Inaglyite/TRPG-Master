from pathlib import Path
import os,sys,json,shutil,tempfile,threading,time,datetime,secrets,asyncio,hashlib,subprocess
ROOT=Path(os.environ['TRPG_TEST_REPO']).resolve();R=Path(os.environ['TRPG_TEST_WORK']).resolve();R.mkdir(parents=True,exist_ok=True);name=sys.argv[1];S=R/name;S.mkdir(parents=True,exist_ok=True);tmp=S/'tmp';tmp.mkdir(exist_ok=True);tempfile.tempdir=str(tmp);os.environ['TMPDIR']=str(tmp)
project=S/'project';project.mkdir(exist_ok=True)
for folder in ['mod','skills','schemas']:
 p=project/folder
 if not p.exists():shutil.copytree(ROOT/folder,p)
(project/'characters').mkdir(exist_ok=True)
if not (project/'characters/default').exists():shutil.copytree(ROOT/'characters/default',project/'characters/default')
for f in ['characters/custom','characters/library','profiles']:(project/f).mkdir(exist_ok=True)
c=json.loads((ROOT/'.env.json').read_text()) if (ROOT/'.env.json').exists() else {};c.update({k:v for k,v in {'api_key':os.environ.get('DEEPSEEK_API_KEY'),'base_url':os.environ.get('DEEPSEEK_BASE_URL','https://api.deepseek.com')}.items() if v});assert c.get('api_key') and 'deepseek.com' in c.get('base_url','')
for k in list(os.environ):
 if k.lower() in ['http_proxy','https_proxy','all_proxy'] or k.startswith(('GLM_','OPENAI_','ANTHROPIC_','TRPG_DATABASE_URL')):os.environ.pop(k,None)
os.environ.update({'TRPG_PROJECT_ROOT':str(project),'TRPG_RUNTIME_ROOT':str(S/'runtime'),'TRPG_WRITE_COMPAT_EXPORTS':'0','OPENAI_API_KEY':c['api_key'],'OPENAI_BASE_URL':c['base_url'],'TRPG_FLASH_MODEL':c.get('flash_model','deepseek-flash'),'TRPG_PRO_MODEL':c.get('flash_model','deepseek-flash'),'TRPG_NARRATIVE_MODEL':c.get('narrative_model','deepseek-flash'),'TRPG_JUDGEMENT_MODEL':c.get('judgement_model','deepseek-flash'),'GLM_API_KEY':'','TRPG_MODEL_TIMEOUT':'90','TRPG_MAX_OUTPUT_TOKENS':'8000','TRPG_TRACE_MAX_OUTPUT':'8000','TRPG_STORY_THINKING':'disabled','TRPG_LOG_DIR':str(S/'logs')})
sys.path[:0]=[str(Path(__file__).parent/'harnesses'),str(ROOT),str(ROOT/'tools')]
# Record actual SDK requests and provider responses. No response or command is substituted.
from openai.resources.chat.completions import Completions
lock=threading.Lock();original=Completions.create;trace=S/'api-audit.jsonl'
def dump(row):
 with lock:
  with trace.open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
def request_record(client,kw):
 return {'endpoint':str(getattr(client,'base_url','')),'model':kw.get('model'),'stream':bool(kw.get('stream')),'temperature':kw.get('temperature'),'max_tokens':kw.get('max_tokens'),'response_format':kw.get('response_format'),'extra_body':kw.get('extra_body'),'messages':kw.get('messages'),'tools':kw.get('tools'),'tool_choice':kw.get('tool_choice')}
class RecordedStream:
 def __init__(self,inner,row):self.inner=inner;self.row=row;self.done=False
 def __getattr__(self,k):return getattr(self.inner,k)
 def __enter__(self):return self
 def __exit__(self,*a):self.close()
 def finish(self):
  if not self.done:self.row['elapsed_s']=round(time.monotonic()-self.row.pop('_start'),4);self.row['phase']='completed';dump(self.row);self.done=True
 def __iter__(self):
  self.row['chunks']=[]
  try:
   for chunk in self.inner:
    d=chunk.model_dump();d.pop('obfuscation',None)
    for choice in d.get('choices') or []:
     if isinstance(choice.get('delta'),dict):choice['delta'].pop('reasoning_content',None)
    self.row['chunks'].append(d)
    if d.get('usage'):self.row['usage']=d['usage']
    if d.get('id'):self.row['response_id']=d['id']
    if d.get('model'):self.row['returned_model']=d['model']
    yield chunk
  except Exception as e:self.row['error_type']=type(e).__name__;raise
  finally:self.finish()
 def close(self):
  try:self.inner.close()
  finally:self.finish()
def audited(self,*args,**kw):
 row={'call_id':secrets.token_hex(8),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'request':request_record(self._client,kw),'_start':time.monotonic()};dump({'phase':'started','call_id':row['call_id'],'model':kw.get('model'),'stream':bool(kw.get('stream')),'started_at':row['started_at']})
 try:
  result=original(self,*args,**kw)
  if kw.get('stream'):return RecordedStream(result,row)
  response=result.model_dump()
  for choice in response.get('choices') or []:
   if isinstance(choice.get('message'),dict):choice['message'].pop('reasoning_content',None)
  row.update(phase='completed',response=response,response_id=result.id,returned_model=result.model,usage=response.get('usage'),elapsed_s=round(time.monotonic()-row.pop('_start'),4));dump(row);return result
 except Exception as e:row.update(phase='error',error_type=type(e).__name__,elapsed_s=round(time.monotonic()-row.pop('_start'),4));dump(row);raise
Completions.create=audited
manifest={'suite':name,'model':os.environ['TRPG_NARRATIVE_MODEL'],'endpoint':c['base_url'],'credential_present':True,'project_root':str(project),'initial_runtime_root':os.environ['TRPG_RUNTIME_ROOT'],'max_output_tokens':8000,'thinking':'disabled','git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'fingerprints':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ['src/structured/agent.py','src/app/agent_graph.py','src/ai/model/model_request.py','src/structured/service.py','src/ai/context/history_compactor.py']}}
(S/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
try:
 if name=='classic':
  import playthrough as m
  report=m.run_playthrough(report_path=S/'report.json',verbose=True)
 elif name=='transition':
  import transition_real_model_check as m
  report=asyncio.run(m.run_trace(report_path=S/'report.json',timeout=240,verbose=True))
 elif name=='memory':
  import memory_real_model_check as m
  report=asyncio.run(m.run(report_path=S/'report.json',timeout=240))
 else:raise ValueError(name)
 print('SUITE_FINISHED',name,flush=True)
except BaseException as e:
 (S/'exception.json').write_text(json.dumps({'type':type(e).__name__,'message':str(e)[:600]},ensure_ascii=False,indent=2));raise
finally:
 rows=[json.loads(l) for l in trace.read_text().splitlines()] if trace.exists() else [];calls=[r for r in rows if r['phase'] in ['completed','error']];summary={'suite':name,'attempts':len(calls),'completed':sum(r['phase']=='completed' for r in calls),'errors':sum(r['phase']=='error' or bool(r.get('error_type')) for r in calls),'returned_models':sorted({r.get('returned_model','') for r in calls}),'prompt_tokens':sum((r.get('usage') or {}).get('prompt_tokens',0) for r in calls),'completion_tokens':sum((r.get('usage') or {}).get('completion_tokens',0) for r in calls),'response_ids':[r['response_id'] for r in calls if r.get('response_id')]};(S/'api-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in summary.items() if k!='response_ids'},ensure_ascii=False),flush=True)
