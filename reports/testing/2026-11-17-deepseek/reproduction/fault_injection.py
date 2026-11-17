from pathlib import Path
import os,json,re,time
R=Path(os.environ['TRPG_TEST_WORK']).resolve();R.mkdir(parents=True,exist_ok=True);import sys;sys.path.insert(0,os.environ['TRPG_TEST_REPO'])
from src.ai.context.context_summary import validate_summary_visibility,validate_summary_shape
plan=json.loads((R/'ablation-plan.json').read_text());rows=[]
for case in plan['cases']:
 for variant in ['exact','whitespace','lowercase']:
  secret=case['secret'];label=secret if case['type']=='attack' else case['public_fact']
  label=' '.join(label) if variant=='whitespace' else label.lower() if variant=='lowercase' else label
  candidate=json.dumps({'events':['调查员抵达大厅。'],'known_facts':[label],'open_threads':[],'current_scene':'钟楼大厅','checks':[]},ensure_ascii=False)
  t=time.perf_counter_ns();result=validate_summary_visibility(candidate,case['world']);elapsed=(time.perf_counter_ns()-t)/1e6
  rows.append({'case':case['id'],'type':case['type'],'variant':variant,'candidate':candidate,'prompt_only_accepts':validate_summary_shape(candidate).allowed,'guard_allowed':result.allowed,'reason':result.reason,'guard_ms':elapsed})
a=[x for x in rows if x['type']=='attack'];c=[x for x in rows if x['type']=='control'];out={'attack_candidates':len(a),'prompt_only_leaks_accepted':sum(x['prompt_only_accepts'] for x in a),'full_guard_leaks_accepted':sum(x['guard_allowed'] for x in a),'controls':len(c),'controls_accepted':sum(x['guard_allowed'] for x in c),'total_guard_ms':sum(x['guard_ms'] for x in rows),'rows':rows}
(R/'fault-injection-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in out.items() if k!='rows'},ensure_ascii=False,indent=2))
