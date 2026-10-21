/* Browser integration test. TEST-ONLY Codex fixture; training/verifier are real. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const python = process.env.FOREMAN_TEST_PYTHON || 'python';
const project = fs.mkdtempSync(path.join(os.tmpdir(), 'foreman-browser-'));
const output = process.env.FOREMAN_BROWSER_OUTPUT || path.join(os.tmpdir(), 'foreman-browser-results');
fs.mkdirSync(output, {recursive:true});
execFileSync(python, ['-c', `from agentforeman.cli import init_demo
from pathlib import Path
import json,sys
p=Path(sys.argv[1]); init_demo(p)
c=p/'foreman.toml'; c.write_text(c.read_text().replace('executable = "codex"','executable = '+json.dumps(sys.argv[2])))`,project,path.join(root,'tests/fixture_campaign.py')], {cwd:root});
const child=spawn(python,['-m','agentforeman','ui','--project',project,'--port','0','--no-open'],{cwd:root,stdio:['ignore','pipe','pipe']});
let browser, page, capability, origin, cid, waitingCid;
const failures=[];
const completed=[];
const until=async (predicate, label, timeout=30000)=>{const end=Date.now()+timeout;while(Date.now()<end){if(await predicate())return;await new Promise(r=>setTimeout(r,150));}throw new Error('Timed out: '+label);};
const request=async (route,body)=>{const options={headers:{'X-Foreman-Token':capability}};if(body!==undefined){options.method='POST';options.headers.Origin=origin;options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}const r=await fetch(origin+route,options);const d=await r.json();assert(r.ok,JSON.stringify(d));return d;};
(async()=>{
  const url=await new Promise((resolve,reject)=>{let s='';const t=setTimeout(()=>reject(new Error('Panel startup timed out')),10000);child.stdout.on('data',d=>{s+=d.toString();const m=s.match(/console: (http:\/\/127\.0\.0\.1:\d+\/#[\w-]+)/);if(m){clearTimeout(t);resolve(m[1]);}});child.on('exit',code=>{clearTimeout(t);reject(new Error('Panel exited '+code));});});
  origin=new URL(url).origin; capability=url.split('#')[1];
  browser=await chromium.launch({headless:true,...(process.env.FOREMAN_BROWSER_EXECUTABLE?{executablePath:process.env.FOREMAN_BROWSER_EXECUTABLE,args:['--no-sandbox','--disable-gpu','--disable-dev-shm-usage']}: {})});
  page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
  page.on('pageerror',e=>failures.push(e.message));
  await page.goto(url); await page.locator('#sync').filter({hasText:'已同步'}).waitFor();
  assert.equal(await page.locator('#empty').isVisible(),true);
  assert.equal(await page.locator('#error').isVisible(),false);
  assert.equal(await page.evaluate(()=>location.hash),'');
  completed.push('Empty state, capability handoff and authenticated local sync');
  await page.locator('#new-task').click();
  const goal='提高验证准确率 · TEST FIXTURE <img src=x onerror="window.injected=true">';
  await page.locator('[name=goal]').fill(goal);await page.locator('[name=target]').fill('0.95');
  await page.locator('#submit-task').click();await page.locator('#create-dialog').waitFor({state:'hidden'});
  await until(async()=>{const o=await request('/api/overview');cid=o.campaigns[0]?.id;return o.campaigns[0]?.status==='completed';},'real training completion');
  await page.locator('#refresh').click();await until(async()=>await page.locator('#task-rows').innerText().then(s=>s.includes('已完成')),'completed task visible');
  assert.equal(await page.evaluate(()=>window.injected),undefined);assert.equal(await page.locator('#task-rows img').count(),0);
  await until(async()=>(await page.locator('#detail').innerText()).includes('0.975'),'verified metric in task detail');
  completed.push('Form submission drives real train/independent verify; untrusted goal is escaped');
  await page.locator('[data-detail-tab=logs]').click();await page.locator('#log-output').filter({hasText:'baseline_train'}).waitFor();
  await page.locator('#log-kind').selectOption('worker');await page.locator('#log-output').filter({hasText:'stdout.log'}).waitFor();
  await page.locator('[data-detail-tab=report]').click();assert((await page.locator('#detail-content').innerText()).includes('Best independently verified metric'));
  await page.locator('[data-detail-tab=events]').click();assert(await page.locator('.event').count()>0);
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('[data-action=archive]').click()]);
  const zip=path.join(output,'browser-downloaded-best.zip');await download.saveAs(zip);
  execFileSync(python,['-m','agentforeman','verify-archive',zip],{cwd:root});
  completed.push('Actual controller/worker logs, report, events and verified archive download');
  await page.locator('#search').fill('no-such-goal');assert.equal(await page.locator('#empty').isVisible(),true);await page.locator('#search').fill('');
  await page.locator('[data-view=usage]').click();assert.equal(await page.locator('.quotaCard').count(),2);
  await page.locator('[data-refresh-role=worker]').click();await until(async()=>!(await page.locator('[data-refresh-role=worker]').isDisabled()),'quota refresh');
  assert((await page.locator('#quota-cards').innerText()).includes('100% 已用'));
  await page.locator('[data-view=analytics]').click();await page.locator('#stats-cards .statCard').first().waitFor();assert((await page.locator('#role-stats').innerText()).includes('数据不完整'));
  await page.locator('[data-view=settings]').click();await page.locator('#checks .checkRow').first().waitFor();assert.equal(await page.locator('#checks .checkRow').count(),4);
  completed.push('Search/filter and quota/statistics/diagnostics pages use real APIs');
  await page.locator('[data-view=tasks]').click();
  await page.locator('#new-task').click();await page.locator('[name=goal]').fill('夜间继续探索：额度恢复后开始下一轮 · TEST ONLY');await page.locator('[name=target]').fill('');await page.locator('[name=preflight]').check();await page.locator('#submit-task').click();await page.locator('#create-dialog').waitFor({state:'hidden'});
  await until(async()=>{const o=await request('/api/overview');const row=o.campaigns.find(r=>r.id!==cid);waitingCid=row?.id;return row?.status==='waiting_quota';},'durable quota wait');
  await page.locator('#refresh').click();await until(async()=>(await page.locator('#detail').innerText()).includes('预计恢复'),'wait card');
  await page.locator('[data-detail-tab=summary]').click();
  await page.screenshot({path:path.join(output,'panel-light.png'),fullPage:true});
  await page.locator('#theme').click();assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark');
  await page.screenshot({path:path.join(output,'panel-dark.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(output,'panel-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.equal(await page.locator('#new-task').isVisible(),true);
  await page.setViewportSize({width:1440,height:1000});
  // Simulate a crashed controller, then use the UI to resume the same durable wait.
  const before=JSON.parse(fs.readFileSync(path.join(project,'.foreman','campaigns',waitingCid,'runner.json'),'utf8'));
  process.kill(before.process.pid,'SIGKILL');
  await until(async()=>!(await request('/api/overview')).campaigns.find(r=>r.id===waitingCid).controller_alive,'controller death reconciled');
  await page.locator('#refresh').click();await until(async()=>!(await page.locator('[data-action=resume]').isDisabled()),'resume enabled');
  await page.locator('[data-action=resume]').click();await until(async()=>(await request('/api/overview')).campaigns.find(r=>r.id===waitingCid).controller_alive,'UI resume');
  await page.locator('[data-action=stop]').click();await until(async()=>(await request('/api/campaigns/'+waitingCid)).status==='completed','UI stop');
  completed.push('Light/dark/mobile layout; killed-controller UI resume and durable quota-wait stop');
  assert.deepEqual(failures,[]);
  fs.writeFileSync(path.join(output,'browser-summary.json'),JSON.stringify({passed:true,checks:completed,page_errors:failures,disclosure:'TEST-ONLY Codex planner/editor/metadata fixtures. Training, checkpoint verification, process restart and archive replay are real.'},null,2));
  console.log(JSON.stringify({passed:true,checks:completed,output},null,2));
})().catch(async error=>{console.error(error);if(page)await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});process.exitCode=1;}).finally(async()=>{
  if(waitingCid)try{await request('/api/campaigns/'+waitingCid+'/stop',{});await until(async()=>(await request('/api/campaigns/'+waitingCid)).status==='completed','cleanup',5000);}catch{}
  if(browser)await browser.close();child.kill('SIGTERM');
});
