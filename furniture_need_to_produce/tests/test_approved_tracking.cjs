const {chromium}=require('/root/.cache/ms-playwright-go/1.57.0/package/index.js');
const fs=require('fs'),assert=require('assert/strict');
(async()=>{
 const cfg=JSON.parse(fs.readFileSync(0,'utf8'));
 assert.match(cfg.db,/^approved_tracking_verify_/,'Cancellation UI tests require an isolated verification database');
 const browser=await chromium.launch({headless:true,executablePath:'/root/.cache/ms-playwright/chromium_headless_shell-1187/chrome-linux/headless_shell',args:['--no-sandbox']});
 try {
  const ctx=await browser.newContext({viewport:{width:1440,height:1000}});
  await ctx.addCookies([{name:'session_id',value:cfg.sid,url:cfg.base}]);
  const page=await ctx.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(cfg.base+'/odoo/action-'+cfg.action+'?db='+cfg.db,{waitUntil:'domcontentloaded',timeout:90000});
  const root=page.locator('.o_ntp_approved');
  await root.waitFor({timeout:90000});
  for(const scope of ['final','stage']) {
   await page.getByRole('tab',{name:scope==='final'?'المنتج التام':'المراحل',exact:true}).click();
   await page.getByRole('searchbox').fill(cfg.labels[scope]);
   await page.getByRole('button',{name:'بحث',exact:true}).click();
   await page.locator('.o_ntp_approved_model_tile').first().waitFor();
   await page.locator('.o_ntp_approved_model_tile').first().click();
   const card=page.locator('.o_ntp_approved_card');
   await card.waitFor({timeout:30000});
   assert.equal(await card.count(),1);
   assert(await card.locator('.o_ntp_approved_step').count()>0);
   await card.getByRole('button',{name:'إلغاء الاعتماد',exact:true}).click();
   await page.getByRole('button',{name:'رجوع',exact:true}).click();
   await page.getByRole('dialog').waitFor({state:'hidden'});
   assert.equal(await card.count(),1);
   await page.screenshot({path:cfg.folder+'/'+scope+'-tracking.png'});
   await card.getByRole('button',{name:'إلغاء الاعتماد',exact:true}).click();
   await Promise.all([page.waitForResponse(r=>r.url().includes('/action_cancel_approval')), page.getByRole('button',{name:'إلغاء الاعتماد وحذف الأوامر',exact:true}).click()]);
   await page.getByRole('dialog').waitFor({state:'hidden'});
   await page.getByRole('heading',{name:'لا توجد اعتمادات مطابقة',exact:true}).waitFor({timeout:30000});
   console.log('PASS UI',scope,'tab; search; live steps; confirmation back; atomic cancellation');
  }
  await page.getByRole('tab',{name:'المنتج التام',exact:true}).click();
  await page.getByRole('searchbox').fill('');
  await page.getByRole('button',{name:'بحث',exact:true}).click();
  const existing=page.locator(`[data-card-id="${cfg.existing_card}"]`);
  await page.locator('.o_ntp_approved_model_tile').first().waitFor();
  const modelIds=await page.locator('.o_ntp_approved_model_tile').evaluateAll(nodes=>nodes.map(n=>n.dataset.modelId));
  for (const modelId of modelIds) {
   await page.locator(`[data-model-id="${modelId}"]`).click();
   await page.locator('.o_ntp_approved_card').first().waitFor();
   if (await existing.count()) break;
   await page.getByRole('button',{name:'كل الموديلات',exact:true}).click();
   await page.locator('.o_ntp_approved_model_tile').first().waitFor();
  }
  await existing.waitFor();
  await existing.getByRole('button',{name:'إلغاء الاعتماد',exact:true}).click();
  await page.getByRole('button',{name:'إلغاء الاعتماد وحذف الأوامر',exact:true}).click();
  await existing.waitFor({state:'detached',timeout:30000});
  console.log('PASS existing cloned approval with pending kits cancelled');
  await page.getByRole('searchbox').fill('');
  await page.getByRole('button',{name:'بحث',exact:true}).click();
  await page.waitForTimeout(1000);
  await page.setViewportSize({width:390,height:844});
  assert(await root.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  assert.deepEqual(errors,[]);
  console.log('PASS mobile layout and no browser errors');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
