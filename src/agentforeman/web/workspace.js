'use strict';
// The three everyday screens share the existing authenticated local API.
let inboxSelected=null, inboxRenderKey='', inboxBusy=false, allocationData=null, allocationDirty=false;
const simpleStatus={...labels,...councilStatus,...modeStatus,...briefingStatus,completed_scoped:'检查范围内通过',completed_candidate:'候选答案已完成',running:'执行中',launched:'已启动',uncertain:'执行状态待确认'};
const simpleStage={...phases,...modeStage,...councilStage,clarify:'需求澄清',launch:'正在启动'};
const providerName=p=>({'codex':'GPT / Codex','claude-code':'Claude','opencode':'OpenCode','antigravity':'Antigravity','gemini-cli':'Gemini CLI','anthropic':'Claude API','openai-compatible':'API / 中转站'}[p]||p);
const tokenText=r=>r&&r.attempts>r.unknown_attempts?num(r.total_tokens):r?.attempts?'未知':'尚无调用';
const shareText=r=>r.share_percent===null?'未知':`${Number(r.share_percent.toFixed(1))}%`;
function chatMessage(name,meta,text){return `<article class="inboxMessage"><span class="inboxAvatar" aria-hidden="true">${esc(name.slice(0,1))}</span><div><div class="inboxMessageHead"><strong>${esc(name)}</strong><small>${esc(meta)}</small></div><p>${esc(text)}</p></div></article>`;}
function taskUsage(data,key){
  const u=data.tasks.find(t=>t.key===key);
  return `<div class="inboxUsage"><span>近 30 天本任务已记录 <strong>${tokenText(u)}</strong>${u?.attempts>u?.unknown_attempts?' tokens':''}${u?.unknown_attempts?` · ${u.unknown_attempts} 次用量未知`:''}</span><div>${(u?.connections||[]).map(c=>`<span>${esc(c.label)} ${shareText(c)}</span>`).join('')}</div></div>`;
}
async function refreshWorkspace(){
  const [box,accounting]=await Promise.all([api('/api/workspace'),api('/api/token-usage?days=30')]);
  if(state.view!=='workspace')return;
  const list=$('#inbox-list');
  if(!box.tasks.some(t=>t.key===inboxSelected))inboxSelected=box.tasks[0]?.key||null;
  list.innerHTML=box.tasks.map(t=>`<button type="button" class="inboxItem" data-inbox-key="${esc(t.key)}" aria-pressed="${t.key===inboxSelected}"><strong>${esc(t.title)}</strong><small>${esc(simpleStatus[t.status]||t.status)} · ${esc(simpleStage[t.stage]||t.stage||'')}</small><small>${date(t.created_at)} · ${t.kind==='briefings'?'主席任务':({campaigns:'夜间实验',debates:'对抗',cooperations:'合作',councils:'委员会',coding:'代码候选'}[t.kind]||'任务')}</small></button>`).join('')||'<p class="muted">尚无任务。</p>';
  if(!inboxSelected){$('#inbox-detail').innerHTML='<div class="inboxEmpty"><h3>从一个目标开始</h3><p>接入至少 3 个可用连接，再和主席讨论目标与验收。</p><div class="detailActions"><button data-view="connections">接入订阅 / API</button><button class="primary" data-open-quick>新建任务</button></div></div>';return;}
  const row=box.tasks.find(t=>t.key===inboxSelected),key=inboxSelected;
  const c=await api(`/api/${row.kind}/${row.id}`);
  let child=null;
  if(row.execution)child=await api(`/api/${row.execution.kind}/${row.execution.id}`);
  if(state.view!=='workspace'||key!==inboxSelected)return;
  const u=accounting.tasks.find(t=>t.key===key),renderKey=JSON.stringify([key,c,child,u]);
  if(renderKey===inboxRenderKey)return;
  // Polling must not discard a human's partially typed clarification.
  const input=$('#inbox-reply'),draft=input?.value||'',same=$('#inbox-detail').dataset.key===key;
  const focused=document.activeElement===input,selection=input?[input.selectionStart,input.selectionEnd]:null;
  const expanded=new Set(Array.from($('#inbox-detail').querySelectorAll('details[open] > summary')).map(n=>n.textContent));
  inboxRenderKey=renderKey;$('#inbox-detail').dataset.key=key;
  const execution=child||c,canStop=['running','ready','waiting_human','waiting_quota','paused'].includes(execution.status);
  let content='';
  if(row.kind==='briefings'){
    const names=Object.fromEntries((c.auto_candidates||[]).map(r=>[r.member,r.label]));
    const chair=names[c.execution.chair]||c.execution.chair;
    content+=c.messages.map(m=>chatMessage(m.role==='human'?'你':chair,m.role==='human'?'需求与补充':'主席',(m.content||'')+([...m.questions||[],...m.open_questions||[]].length?'\n待澄清：'+[...m.questions||[],...m.open_questions||[]].join('；'):''))).join('');
    if(c.draft){
      content+=`<section class="inboxContract"><h3>任务约定 v${c.version}</h3><p>${esc(c.draft.contract.goal)}</p><div class="contractColumns"><div><strong>交付物</strong><ul>${c.draft.contract.deliverables.map(d=>'<li>'+esc(d.description)+'</li>').join('')}</ul></div><div><strong>验收标准</strong><ul>${c.draft.contract.acceptance.map(a=>'<li>'+esc(a.criterion)+(a.verification==='automated'?' · 程序检查':' · 待人工验收')+'</li>').join('')}</ul></div></div><details><summary>完整约定、分工与预算</summary>${contractView(c.draft.contract)}<p>模式：${c.execution.mode==='cooperative'?'合作':'对抗'} · 主席 ${esc(chair)} · ${c.execution.budget_seconds/3600} 小时 · 最多 ${c.execution.max_rounds} 轮</p><p>成员：${esc(c.execution.members.map(m=>names[m]||m).join('、'))}${c.execution.reviewers?.length?' · 审查者：'+esc(c.execution.reviewers.map(m=>names[m]||m).join('、')):''}</p>${c.draft.role_plan?'<p>分工理由：'+esc(c.draft.role_plan.rationale)+'</p>':''}</details></section>`;
      if(c.status==='ready')content+=`<div class="inboxConfirm"><span class="muted">确认将冻结当前约定、分工、资料与检查。</span><button class="primary" data-inbox-confirm="${c.version}" ${inboxBusy?'disabled':''}>确认 v${c.version} 并开工</button><button data-inbox-edit>编辑完整约定</button></div>`;
    }
    if(c.errors?.length)content+='<details open><summary>需要处理</summary><pre>'+esc(JSON.stringify(c.errors,null,2))+'</pre></details>';
    if(child)content+=executionView(child,row.execution.kind);
    if(['waiting_human','ready','running'].includes(c.status))content+=`<form id="inbox-reply-form"><label class="srOnly" for="inbox-reply">和主席讨论任务</label><textarea id="inbox-reply" rows="2" maxlength="8000" required placeholder="补充目标、资料或验收要求…" ${c.status==='running'?'disabled':''}></textarea><div><span class="muted">${c.status==='running'?'主席整理中，等待回复后可继续补充':'发给主席 · 变更后重新确认'}</span><button class="primary" type="submit" ${inboxBusy||c.status==='running'?'disabled':''}>发送 ↑</button></div></form>`;
    else content+='<p class="muted">已确认的执行约定保持冻结。调整方向可先终止当前任务，再新建任务。</p>';
  }else if(['debates','cooperations'].includes(row.kind))content=executionView(c,row.kind);
  else if(row.kind==='councils'){
    content=chatMessage('主席','综合意见',c.stages?.synthesize?.[0]?.result?.answer||'等待成员独立探索与评议。');
    content+='<p class="muted">模型共识不等于事实核验；独立审查与完整记录见详细结果。</p>';
  }else if(row.kind==='campaigns'){
    content=chatMessage('实验控制器',simpleStage[c.phase]||c.phase,c.goal);
    content+=`<div class="inboxEvent">已记录 ${c.rounds.length} / ${c.max_rounds} 轮 · 最佳独立验证指标 ${metric(c.best_metric)}</div>`;
    content+=c.rounds.map(r=>chatMessage('第 '+r.number+' 轮',simpleStatus[r.status]||r.status,r.decision?.rationale||r.error||'已保存实验记录')).join('');
  }else content=chatMessage('代码候选',c.status,`基线 ${metric(c.baseline?.metric)} · 最佳 ${metric(c.best?.metric)}。各成员独立实现，由本机训练与验证选取结果。`);
  $('#inbox-detail').innerHTML=`<header class="inboxHead"><div><h3>${esc(row.title)}</h3><small>${esc(!child&&row.kind==='briefings'&&execution.status==='running'?'主席整理中':simpleStatus[execution.status]||execution.status)} · ${esc(simpleStage[execution.stage||execution.phase]||execution.stage||execution.phase||'')} · ${esc(row.id)}</small></div><div>${canStop?terminationControls(row.kind,row.id):terminationControls(row.kind,row.id).replace(/<button[^>]*>[\s\S]*?<\/button>/,'')}</div></header><div class="inboxBody">${content}</div>${taskUsage(accounting,key)}<div class="inboxFoot"><button data-inbox-export>导出记录</button><button data-inbox-advanced>详细结果与日志</button></div>`;
  if(same){
    if($('#inbox-reply')&&draft)$('#inbox-reply').value=draft;
    if(focused&&$('#inbox-reply')&&!$('#inbox-reply').disabled){$('#inbox-reply').focus({preventScroll:true});$('#inbox-reply').setSelectionRange(...selection);}
    $('#inbox-detail').querySelectorAll('details > summary').forEach(n=>{if(expanded.has(n.textContent))n.parentElement.open=true;});
  }
  $('#inbox-detail')._data={row,c,child};
}
function executionView(c,kind){
  const selected=c.versions.find(v=>v.id===c.selected_version)||c.versions.at(-1);
  const artifacts=selected?.proposal?.artifacts||[];
  const checks=selected?.checks||[];
  let s=`<div class="inboxEvent">${kind==='cooperations'?'合作执行':'对抗执行'} · ${esc(simpleStage[c.stage]||c.stage)} · 第 ${c.round}/${c.max_rounds} 轮</div>`;
  if(c.plan)s+=chatMessage('主席','拆分计划',c.plan.summary||c.plan.rationale||'已保存成员分工与集成约定。');
  if(selected)s+=chatMessage('执行者','当前产物',selected.proposal.summary||'已提交候选产物，等待独立检查。');
  s+=artifacts.map(a=>'<details class="inboxArtifact"><summary>'+esc(a.id)+' · 查看产物</summary><pre>'+esc(a.content)+'</pre></details>').join('');
  if(checks.length)s+='<section class="inboxArtifact"><h3>独立验证</h3><p>'+checks.filter(r=>r.passed===true).length+' / '+checks.length+' 项检查通过 · 仅验收已配置范围</p><details><summary>查看验证证据</summary><pre>'+esc(JSON.stringify(checks,null,2))+'</pre></details></section>';
  if(c.reviews?.length)s+='<details class="inboxArtifact"><summary>批评与修改 · '+c.reviews.length+' 份审查</summary><pre>'+esc(JSON.stringify(c.reviews,null,2))+'</pre></details>';
  const pending=c.pending_questions||c.issues?.filter(i=>i.status!=='check_passed_scoped')||[];
  if(pending.length)s+='<details class="inboxArtifact" open><summary>待验证问题 · '+pending.length+'</summary><pre>'+esc(JSON.stringify(pending,null,2))+'</pre></details>';
  if(c.stop_reason)s+='<p class="muted">结束原因：'+esc(c.stop_reason)+'</p>';
  return s;
}
async function openQuick(){await refreshConnections();$('#quick-dialog').showModal();$('#quick-task-form [name=idea]').focus();}
$('#inbox-new').onclick=()=>openQuick().catch(e=>showError(e.message));
$('#open-connection').onclick=()=>{$('#connection-dialog').showModal();connectionGuide();};
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b||b.disabled)return;
  try{
    if(b.dataset.closeDialog){$('#'+b.dataset.closeDialog).close();return;}
    if(b.hasAttribute('data-open-quick'))return await openQuick();
    if(b.dataset.inboxKey){inboxSelected=b.dataset.inboxKey;inboxRenderKey='';return await refreshWorkspace();}
    const d=$('#inbox-detail')._data;
    if(!d)return;
    if(b.hasAttribute('data-inbox-export'))return await download(null,d.row.id+'-task.json',{briefing:d.c,execution:d.child});
    if(b.hasAttribute('data-inbox-advanced')||b.hasAttribute('data-inbox-edit')){
      if(d.row.kind==='campaigns'){await navigate('tasks');return await select(d.row.id);}
      await navigate('council');
      if(d.row.kind==='briefings'){briefingRenderKey='';await selectBriefing(d.row.id);$('#briefing-workspace').scrollIntoView({block:'start'});}
      else if(['debates','cooperations'].includes(d.row.kind)){await selectMode(d.row.kind,d.row.id);$('#mode-detail').scrollIntoView({block:'start'});}
      else if(d.row.kind==='councils')await selectCouncil(d.row.id);else await selectCoding(d.row.id);
      return;
    }
    if(b.hasAttribute('data-inbox-confirm')){
      if(inboxBusy)return;inboxBusy=true;b.disabled=true;
      try{await api(`/api/briefings/${d.row.id}/confirm`,{version:d.c.version,sha256:d.c.sha256});inboxRenderKey='';await refreshWorkspace();notice('已保存确认。任务按已确认约定启动，可在此查看执行进展。');}
      finally{inboxBusy=false;b.disabled=false;}
    }
  }catch(e){showError(e.message);}
});
document.addEventListener('submit',async event=>{
  if(event.target.id!=='inbox-reply-form')return;event.preventDefault();if(inboxBusy)return;
  const d=$('#inbox-detail')._data,body=$('#inbox-reply').value;
  inboxBusy=true;event.target.querySelector('button').disabled=true;
  try{await api(`/api/briefings/${d.row.id}/message`,{version:d.c.version,content:body});$('#inbox-reply').value='';inboxRenderKey='';await refreshWorkspace();}
  catch(e){showError(e.message);}finally{inboxBusy=false;if($('#inbox-reply-form button'))$('#inbox-reply-form button').disabled=false;}
});
function renderConnectionCard(c,statuses){
  const local=['codex','claude-code','gemini-cli','antigravity','opencode'].includes(c.provider);
  const status=c.testing?'测试中…':!c.key_available?'需要 API 密钥':c.retry_at>Date.now()/1000?'额度等待至 '+date(c.retry_at):!c.enabled?(statuses[c.test?.status]||'尚未测试')+' · 未启用':!c.active_now?'等待可用时段':c.worker_running?'已连接':'后台节点未运行';
  return `<article class="statCard"><strong>${esc(c.label)}</strong><span>${esc(providerName(c.provider))}${c.model?' · '+esc(c.model):' · 默认模型'} · ${esc(status)}</span><small>账号：${esc(c.account_label||(local?'本机当前登录':'API 连接'))}</small><div class="detailActions"><button data-connection-test="${c.id}" ${c.testing?'disabled':''}>${c.test?'重新测试':'测试并启用'}</button><button data-connection-enable="${c.id}" data-enabled="${!c.enabled}" ${!c.test?.passed?'disabled':''}>${c.enabled?'暂停':'启用'}</button></div><details><summary>连接详情${!local?' / 更新密钥':''}</summary><p>共享资源：${esc(c.resource_group)}${c.secret_storage?' · 密钥：'+esc(c.secret_storage==='os'?'系统凭据库':c.secret_storage==='session'?'仅本次进程':'环境变量'):''}</p>${c.notice?'<p>'+esc(c.notice)+'</p>':''}${c.test?'<p>'+esc(c.test.diagnostic)+'</p>':''}${!local?`<form class="connectionKeyForm" data-connection-key="${c.id}"><label class="field">更新密钥（仅本次进程）<input type="password" name="api_key" required autocomplete="new-password" maxlength="8192"></label><button type="submit">更新后重新测试</button></form>`:''}</details></article>`;
}
async function refreshAllocation(){
  if(allocationDirty)return;
  allocationData=await api('/api/preferences');$('#allocation-reload').hidden=true;
  $('#allocation-fields').innerHTML=allocationData.connections.map(c=>`<label class="allocationField"><span>${esc(c.label)}<small>${esc(providerName(c.provider))}</small></span><span><input type="number" aria-label="${esc(c.label)} 的目标占比" min="0" max="100" step="1" required value="${c.target_share}" data-allocation-id="${c.id}"> %</span></label>`).join('')||'<p class="muted">添加连接后可设置目标占比。</p>';
  allocationTotal();
}
function allocationTotal(){
  const inputs=Array.from(document.querySelectorAll('[data-allocation-id]')),total=inputs.reduce((s,n)=>s+Number(n.value),0);
  const valid=inputs.length>0&&total===100&&inputs.every(n=>Number.isInteger(Number(n.value))&&Number(n.value)>=0&&Number(n.value)<=100);
  $('#allocation-status').textContent=inputs.length?`合计 ${num(total)}%${valid?' · 仅影响新任务':' · 请调整为 100%'}`:'尚无连接';
  $('#allocation-save').disabled=!valid;return valid;
}
$('#allocation-reload').onclick=()=>{allocationDirty=false;refreshAllocation().catch(e=>showError(e.message));};
$('#allocation-form').oninput=()=>{allocationDirty=true;$('#allocation-reload').hidden=false;allocationTotal();};
$('#allocation-form').onsubmit=async e=>{
  e.preventDefault();if(!allocationTotal())return;const b=$('#allocation-save');b.disabled=true;
  try{const weights=Object.fromEntries(Array.from(document.querySelectorAll('[data-allocation-id]')).map(n=>[n.dataset.allocationId,Number(n.value)]));await api('/api/preferences',{version:allocationData.version,weights});allocationDirty=false;await refreshAllocation();notice('分配偏好已保存。新任务会向主席提供目标比例，实际消耗请在用量页查看。');}
  catch(err){showError(err.message+'；可切换页面重新加载偏好。');}finally{allocationTotal();}
};
const usageSeries=c=>({'codex':0,'claude-code':1,'anthropic':1,'opencode':2,'antigravity':3,'gemini-cli':3,'openai-compatible':4}[c.provider]??5);
async function renderTokenUsage(){
  const days=$('#usage-period').value, u=await api('/api/token-usage?days='+days);if($('#usage-period').value!==days)return;
  state.stats=u;const t=u.totals;
  $('#usage-summary').innerHTML=`<div><span>已记录 tokens</span><strong>${tokenText(t)}</strong></div><div><span>输入 / 输出</span><b>${t.attempts>t.unknown_attempts?num(t.input_tokens)+' / '+num(t.output_tokens):'未知'}</b></div><div><span>统计覆盖</span><b>${u.connections.length} 个连接 · ${u.tasks.length} 个任务</b></div>`;
  $('#usage-split').innerHTML=t.total_tokens?`<div class="usageSplit" role="img" aria-label="已上报 token 占比，未知调用不在分母中">${u.connections.filter(c=>c.total_tokens).map((c,i)=>`<span class="series-${usageSeries(c)}" style="width:${c.share_percent}%" title="${esc(c.label)} ${shareText(c)}"></span>`).join('')}</div>`:'<p class="muted">尚无已上报的 token 数据。</p>';
  $('#usage-connections').innerHTML=u.connections.map((c,i)=>`<div><span><i class="usageDot series-${usageSeries(c)}"></i>${esc(c.label)} <small>${shareText(c)}</small></span><strong>${tokenText(c)}</strong><small>${esc(providerName(c.provider))}${c.model?' · '+esc(c.model):''}${c.unknown_attempts?' · '+c.unknown_attempts+' 次未知':''}${c.partial_attempts?' · '+c.partial_attempts+' 次部分记录':''}</small></div>`).join('');
  $('#usage-task-table').innerHTML=u.tasks.length?`<table class="simpleTable"><thead><tr><th>任务</th><th>各连接占比（已上报部分）</th><th>合计 tokens</th><th>未知调用</th></tr></thead><tbody>${u.tasks.map(r=>`<tr><td>${r.selectable?`<button class="usageTaskLink" data-usage-task="${esc(r.key)}">${esc(r.title)}</button>`:esc(r.title)}</td><td>${r.connections.map(c=>esc(c.label)+' '+shareText(c)).join('<br>')}</td><td>${tokenText(r)}</td><td>${r.unknown_attempts||'—'}</td></tr>`).join('')}</tbody></table>`:'<p class="muted">此时段尚无调用记录。</p>';
  $('#usage-coverage').textContent=`${t.attempts} 次调用尝试 · ${t.unknown_attempts} 次用量未知 · ${t.partial_attempts} 次仅有部分记录。占比不含未知部分，不能表示费用或套餐剩余额度。`;
}
$('#usage-period').onchange=()=>renderTokenUsage().catch(e=>showError(e.message));
document.addEventListener('click',async e=>{const b=e.target.closest('[data-usage-task]');if(!b)return;inboxSelected=b.dataset.usageTask;inboxRenderKey='';await navigate('workspace');});
