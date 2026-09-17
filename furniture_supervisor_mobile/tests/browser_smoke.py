import json,os
from playwright.sync_api import sync_playwright
entries=json.load(open(os.environ['MAPILLA_TEST_FIXTURES']))
base=os.environ['MAPILLA_TEST_URL']
assert 'codex_supervisor_' in base, 'Run mutation smoke tests only on an isolated test database.'
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH'),args=['--no-sandbox'])
 for idx,entry in enumerate(entries):
  c=b.new_context(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
  page=c.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(base);page.get_by_test_id('login-user').wait_for()
  if idx==0:page.screenshot(path=os.environ.get('MAPILLA_TEST_ARTIFACTS','/tmp')+'/login-mobile.png')
  assert page.locator('iframe').count()==0
  page.get_by_test_id('login-user').fill(entry['login']);page.get_by_test_id('login-password').fill(entry['password']);page.get_by_test_id('login-submit').click()
  page.get_by_role('button',name='يومي',exact=True).wait_for(timeout=60000)
  page.get_by_text('آخر تحديث',exact=False).wait_for(timeout=60000)
  assert 'متصل بنظام المصنع' in page.locator('body').inner_text()
  if idx==0:
   page.screenshot(path=os.environ.get('MAPILLA_TEST_ARTIFACTS','/tmp')+'/home-mobile.png')
   card=page.get_by_role('button',name='تفاصيل ',exact=False).first
   card.click();page.get_by_role('button',name='إغلاق التفاصيل').wait_for()
   bom=page.get_by_role('button',name='تفاصيل الخامات المطلوبة',exact=True)
   if bom.count():
    bom.click();page.get_by_text('تفاصيل الخامات',exact=False).first.wait_for();page.wait_for_timeout(800)
    page.screenshot(path=os.environ.get('MAPILLA_TEST_ARTIFACTS','/tmp')+'/bom-mobile.png')
    page.get_by_role('button',name='إغلاق التفاصيل').last.click();page.wait_for_timeout(500)
   if page.get_by_role('button',name='إغلاق التفاصيل').count():page.get_by_role('button',name='إغلاق التفاصيل').last.click();page.wait_for_timeout(500)
  for tab in ['الشغل','الخامات','التحديثات','حسابي']:
   page.get_by_role('button',name=tab,exact=True).click();page.wait_for_timeout(120)
  page.get_by_role('button',name='تسجيل الخروج',exact=True).click();page.get_by_role('button',name='تأكيد',exact=True).click()
  page.get_by_test_id('login-user').wait_for(timeout=15000)
  assert not errors,errors
  assert not any('/web/assets/' in r for r in page.evaluate('performance.getEntriesByType("resource").map(x=>x.name)'))
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  print(entry['stage']+' mobile login, tabs, logout PASS',flush=True)
  c.close()
 c=b.new_context(viewport={'width':1440,'height':1000});page=c.new_page();page.goto(base);page.get_by_test_id('login-user').wait_for();page.screenshot(path=os.environ.get('MAPILLA_TEST_ARTIFACTS','/tmp')+'/login-desktop.png');c.close();b.close()
