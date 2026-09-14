const {chromium}=require('playwright'); const fs=require('fs');
(async()=>{const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:390,height:900},deviceScaleFactor:1});const errors=[];page.on('pageerror',e=>errors.push(e.message));
let revision=0;
const workspace={version:'26.11.0',taskId:'task',graphHash:'g1',headHash:'h0',allStepsSubmitted:false,steps:['核对关键词','调整投放','验证结果'].map((title,i)=>({contentHash:'step-'+i,node:{nodeKey:'O'+i,nodeHash:'n'+i,planActionRefs:['P'+i],title,instruction:'核对执行对象，按已确认方案操作并上传凭证。',sequence:i,owner:'运营',executionObject:'关键词投放计划',acceptanceActions:['核对后台记录'],stopConditionRefs:['风险边界'],rollback:'恢复原配置'},status:'pending',records:[],reviews:[]}))};
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
await page.goto('https://workspace.test'); await page.addStyleTag({content:'body{margin:12px;font-family:system-ui}button,input,textarea{font:inherit}.report-hero{padding:20px}.report-hero h2{font-size:22px}.page-section{margin:12px 0}.section-header{display:flex;justify-content:space-between}details>summary{cursor:pointer}'}); await page.addStyleTag({content:fs.readFileSync('web_demo/sop-ui.css','utf8')});await page.evaluate(()=>{window.AppShell={escape:v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))};window.AppRouter={stateFromHash:()=>({taskId:'task'}),navigate:()=>{},schedule:()=>{}};window.AppApi={taskReport:async()=>({sopEvidence:{cards:[{kind:'PLAN',field:'expectedOutcome',nodeKey:'P0',label:'本步骤目标',value:{roi:{expectedValue:3}}},{kind:'PLAN',field:'baseline',nodeKey:'P0',label:'本步骤基线',value:{roi:{value:2,unit:'ratio'}}},{kind:'PLAN',field:'expectedOutcome',nodeKey:'P1',label:'其他步骤目标',value:{roi:{expectedValue:5}}},{kind:'PRESET',label:'企业预设',value:{category:'服饰',planningPreset:{metric:'roi',targetValue:2.8,unit:'ratio',baseline:2,targetFormula:'baseline * 1.4'}}}]},title:'关键词投放效率优化',taskStatus:'执行中',relatedTask:{id:'task',status:'执行中',title:'关键词投放效率优化'}})};});await page.addScriptTag({content:fs.readFileSync('web_demo/modules/task-report/page.js','utf8')});await page.evaluate(async()=>{document.querySelector('#app').innerHTML=await TaskReportPage.render({state:{taskId:'task'}});TaskReportPage.mount({delegate:(sel,event,fn)=>document.addEventListener(event,e=>{const t=e.target.closest(sel);if(t)fn(e,t);})});});await page.locator('#step-result-form textarea').fill('已核对当前关键词');await page.locator('[data-step-key="O1"]').click();await page.locator('[data-step-key="O0"]').click();if(await page.locator('#step-result-form textarea').inputValue()!=='已核对当前关键词')throw Error('draft lost');const scopedCards=page.locator('.step-current > details').nth(1).locator(':scope > .sop-evidence > .sop-evidence-card');
if(await scopedCards.count()!==2)throw Error('step evidence leaked unrelated nodes');
await page.getByText('本步骤数据与方案依据',{exact:true}).click();
await page.getByText('企业预设与方案差异',{exact:true}).click();
await page.locator('.preset-comparison > summary').click();
if(!(await page.locator('.preset-comparison tbody').innerText()).includes('P0'))throw Error('preset comparison missing');
await page.getByText('本步骤数据与方案依据',{exact:true}).click();
await page.locator('#step-result-form input[type=file]').setInputFiles({name:'evidence.txt',mimeType:'text/plain',buffer:Buffer.from('evidence')});
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
await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'dist/v2611-mobile.png',fullPage:true});console.log(JSON.stringify({errors,overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),stepCount:await page.locator('[data-step-key]').count(),draftPreserved:true,submissionReviewResubmission:true}));await page.route('**/api/ops/experience-overview',r=>r.fulfill({json:{initialized:true,totalCount:2,recentLimit:30,groups:[{status:'enabled',domain:'strategy_outcomes',sourceType:'runtime',count:1},{status:'seed',domain:'decision_patterns',sourceType:'seed',count:1}],recent:[{experienceId:'exp1',domain:'strategy_outcomes',status:'enabled',sourceType:'runtime',sourceTaskId:'task'}]}}));
await page.route('**/api/system/knowledge-center/overview',r=>r.fulfill({json:{}}));
await page.addScriptTag({content:fs.readFileSync('web_demo/modules/knowledge-center/page.js','utf8')});
await page.evaluate(async()=>{document.querySelector('#app').innerHTML=await KnowledgeCenterPage.render();});
if(await page.locator('[data-kc-task="task"]').count()!==1)throw Error('experience task link missing');
if(!(await page.locator('.kc-state-grid').first().innerText()).includes('已启用'))throw Error('runtime lifecycle missing');
await page.addScriptTag({content:fs.readFileSync('web_demo/modules/dashboard/page.js','utf8')});
await page.evaluate(async()=>{AppApi.dashboard=async()=>({taskQueue:[{id:'task',title:'测试任务',status:'执行中'}],neuralOperating:{signalCounts:{executing:99}}});document.querySelector('#app').innerHTML=await DashboardPage.render();});
const counts=await page.locator('.dashboard-status-band strong').allTextContents();
if(counts.join(',')!=='0,1,0,0')throw Error('dashboard count is not task-derived: '+counts);
console.log(JSON.stringify({knowledgeRuntimeSource:true,dashboardTaskCounts:true}));
await browser.close();if(errors.length)process.exitCode=1;})();
