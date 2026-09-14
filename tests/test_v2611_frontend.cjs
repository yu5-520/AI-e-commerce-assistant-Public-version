const {chromium}=require('playwright'); const fs=require('fs');
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:390,height:900},deviceScaleFactor:1});const errors=[];page.on('pageerror',e=>errors.push(e.message));
let revision=0;
const workspace={version:'26.11.0',taskId:'task',graphHash:'g1',headHash:'h0',allStepsSubmitted:false,steps:['核对关键词','调整投放','验证结果'].map((title,i)=>({contentHash:'step-'+i,node:{nodeKey:'O'+i,nodeHash:'n'+i,title,instruction:'核对执行对象，按已确认方案操作并上传凭证。',sequence:i,owner:'运营',executionObject:'关键词投放计划',acceptanceActions:['核对后台记录'],stopConditionRefs:['风险边界'],rollback:'恢复原配置'},status:'pending',records:[],reviews:[]}))};
await page.route('**/*',route=>{
 const req=route.request(),url=req.url();
 if(url.includes('/steps')) {
  if(req.method()==='POST') {
   const body=req.postDataJSON(),step=workspace.steps.find(s=>s.node.nodeKey===body.nodeKey);
   revision++;workspace.headHash='h'+revision;step.contentHash='step-'+body.nodeKey+'-'+revision;
   if(url.endsWith('/review')) {step.reviews.push({...body,reviewedAt:'2026-09-14'});step.status=body.decision==='approve'?'completed':'returned';}
   else {step.records.push({...body,recordHash:'record-'+revision,submittedAt:'2026-09-14',attachments:body.attachments.map(a=>({name:a.name,size:8,contentHash:'attachment'}))});step.status='submitted';}
   workspace.allStepsSubmitted=workspace.steps.every(s=>['submitted','completed'].includes(s.status));
  }
  return route.fulfill({json:workspace});
 }
 return route.fulfill({contentType:'text/html',body:'<html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><main id="app"></main></body></html>'});
});
await page.goto('http://workspace.test'); await page.addStyleTag({content:'body{margin:12px;font-family:system-ui}button,input,textarea{font:inherit}.report-hero{padding:20px}.report-hero h2{font-size:22px}.page-section{margin:12px 0}.section-header{display:flex;justify-content:space-between}details>summary{cursor:pointer}'}); await page.addStyleTag({content:fs.readFileSync('web_demo/sop-ui.css','utf8')});await page.evaluate(()=>{window.AppShell={escape:v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))};window.AppRouter={stateFromHash:()=>({taskId:'task'}),navigate:()=>{},schedule:()=>{}};window.AppApi={taskReport:async()=>({title:'关键词投放效率优化',taskStatus:'执行中',relatedTask:{id:'task',status:'执行中',title:'关键词投放效率优化'}})};});await page.addScriptTag({content:fs.readFileSync('web_demo/modules/task-report/page.js','utf8')});await page.evaluate(async()=>{document.querySelector('#app').innerHTML=await TaskReportPage.render({state:{taskId:'task'}});TaskReportPage.mount({delegate:(sel,event,fn)=>document.addEventListener(event,e=>{const t=e.target.closest(sel);if(t)fn(e,t);})});});await page.locator('#step-result-form textarea').fill('已核对当前关键词');await page.locator('[data-step-key="O1"]').click();await page.locator('[data-step-key="O0"]').click();if(await page.locator('#step-result-form textarea').inputValue()!=='已核对当前关键词')throw Error('draft lost');await page.locator('#step-result-form input[type=file]').setInputFiles({name:'evidence.txt',mimeType:'text/plain',buffer:Buffer.from('evidence')});
await page.locator('#step-result-form button[type=submit]').click();
await page.getByText('记录已保存，等待验收',{exact:true}).waitFor();
if(await page.locator('a[download="evidence.txt"]').count()!==1)throw Error('attachment missing');
await page.locator('#step-review-form textarea').fill('补充执行说明');
await page.locator('#step-review-form button[value=return]').click();
await page.getByText('验收记录已保存',{exact:true}).waitFor();
if(!(await page.locator('[data-step-key="O0"]').innerText()).includes('待补交'))throw Error('return status missing');
await page.locator('#step-result-form textarea').fill('已补充执行说明');
await page.locator('#step-result-form button[type=submit]').click();
await page.getByText('记录已保存，等待验收',{exact:true}).waitFor();
await page.locator('#step-review-form textarea').fill('凭证完整');
await page.locator('#step-review-form button[value=approve]').click();
await page.getByText('验收记录已保存',{exact:true}).waitFor();
if(!(await page.locator('[data-step-key="O0"]').innerText()).includes('已验收'))throw Error('approval status missing');
if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('mobile overflow');
await page.screenshot({path:'dist/v2611-mobile.png',fullPage:true});console.log(JSON.stringify({errors,overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),stepCount:await page.locator('[data-step-key]').count(),draftPreserved:true,submissionReviewResubmission:true}));await browser.close();if(errors.length)process.exitCode=1;})();
