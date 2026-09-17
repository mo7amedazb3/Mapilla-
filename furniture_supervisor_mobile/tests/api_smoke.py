import requests,json,uuid,os
DB=os.environ['MAPILLA_TEST_DB']; BASE=os.environ['MAPILLA_TEST_ORIGIN'].rstrip('/')+'/supervisor-app/api/'
assert DB.startswith('codex_supervisor_'), 'Mutations require an isolated test database.'
fixtures=json.load(open(os.environ['MAPILLA_TEST_FIXTURES']))
results=[]
for item in fixtures:
 s=requests.Session();s.headers.update({'X-Mapilla-App':'1'})
 def rpc(op,**data):
  return s.post(BASE+op,json={'jsonrpc':'2.0','method':'call','params':dict(db=DB,**data)},timeout=90).json()
 def good(op,**data):
  r=rpc(op,**data)
  if 'error' in r: raise Exception(op+': '+r['error']['data']['message'])
  return r['result']
 login=good('login',login=item['login'],password=item['password'],native=True)
 assert 'token' in login
 s.headers['Authorization']='Bearer '+login['token']
 assert 'mapilla_supervisor_session' not in s.cookies
 profile=good('profile'); stage=item['stage']
 assert stage in [x['code'] for x in profile['stages']]
 d=good('dashboard',stage=stage);mat=good('requests');warnings=good('warnings')
 jobs=d['product_batches'] if d.get('batch_supervisor_mode') else d['orders']
 report={'stage':stage,'jobs':len(jobs),'requests':len(mat['rows']),'warnings':len(warnings),'checks':[]}
 for action in ['bom','warning']:
  candidate=None
  for j in jobs:
   if j.get('batch_token'):
    if action=='bom' and not j.get('can_open_bom'):continue
    if action=='warning' and stage not in ['painting','carpentry','bases','finishing','upholstery','packaging']:continue
    candidate=dict(batch_token=j['batch_token']);break
   for p in j.get('product_lines',[]):
    if action=='bom' and not p.get('can_open_bom'):continue
    if action=='warning' and stage not in ['painting','carpentry','bases','finishing','upholstery','packaging']:continue
    candidate=dict(order_id=j['id'],line_id=p['production_line_id']);break
   if candidate:break
  if candidate:
   r=rpc('action',stage=stage,action=action,request_id=str(uuid.uuid4()),**candidate)
   if 'error' in r: report['checks'].append({action:r['error']['data']['message']})
   else:
    form=r['result'].get('form',{});report['checks'].append({action:form.get('kind'), 'lines':len(form.get('lines',[])), 'targets':len(form.get('targets',[]))})
    if action=='warning' and form.get('targets'):
     message='اختبار التطبيق المستقل على نسخة الاختبار فقط'
     args=dict(stage=stage,grant=form['grant'],target=form['targets'][0]['id'],message=message,request_id=str(uuid.uuid4()))
     first=good('wizard',**args);second=good('wizard',**args);assert first==second
     report['checks'].append({'warning_sent_and_retry_deduplicated':True})
 for row in mat['rows']:
  if row.get('can_receive') and row['stage_code']==stage:
   r=rpc('action',stage=stage,action='receipt',request_key=row['key'],request_id=str(uuid.uuid4()))
   report['checks'].append({'receipt':r.get('result',{}).get('form',{}).get('kind') or r.get('error',{}).get('data',{}).get('message')});break
 # Explicit deny generic / wrong stage.
 assert 'error' in rpc('unlink')
 if not profile['is_manager']:
  foreign=next(x for x in ['priming','tailoring','packaging'] if x not in [p['code'] for p in profile['stages']])
  assert 'error' in rpc('dashboard',stage=foreign)
 token=login['token'];good('logout');assert 'error' in rpc('profile')
 results.append(report)
 print(json.dumps(report,ensure_ascii=False),flush=True)
open(os.environ.get('MAPILLA_TEST_ARTIFACTS','/tmp')+'/api-results.json','w').write(json.dumps(results,ensure_ascii=False,indent=2))
# unauthenticated and cross-origin
r=requests.post(BASE+'profile',headers={'X-Mapilla-App':'1','Origin':'https://foreign.invalid'},json={'params':{'db':DB}}).json();assert 'error' in r
print('Authentication, logout/revocation, generic RPC and stage isolation PASS')
