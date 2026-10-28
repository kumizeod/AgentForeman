/* Portions ported from 170-carry/codex-tools (MIT), copyright (c) 2026 170-carry:
 * ClassicTopBar, WorkspaceIcon and ClassicUsageMeter. See upstream/LICENSE and NOTICE.md.
 * AgentForeman API integration and experiment controls copyright AgentForeman contributors.
 */
'use strict';
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
  analytics:'M4 3v17h17 M8 15v-4 M13 15V7 M18 15v-6',
  refresh:'M20 7a8 8 0 1 0 1 7 M20 3v5h-5', plus:'M12 5v14 M5 12h14',
  search:'M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0 M15 15l6 6',
  info:'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0 M12 11v6 M12 7h.01',
  close:'M6 6l12 12 M18 6 6 18', check:'m5 12 4 4L19 6',
  sun:'M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.4 1.4 M17.6 17.6 19 19 M5 19l1.4-1.4 M17.6 6.4 19 5',
  moon:'M20.5 14.6A8.5 8.5 0 0 1 9.4 3.5 8.7 8.7 0 1 0 20.5 14.6Z',
};
const icon = name => `<svg class="workspaceIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
document.querySelectorAll('[data-icon]').forEach(n => { n.innerHTML = icon(n.dataset.icon); });
const labels = {ready:'已提交', running:'运行中', paused:'已暂停', waiting_quota:'等待额度', completed:'已完成', failed:'需处理'};
const phases = {baseline:'基线评估',decide:'规划下一轮',review:'独立审查',revise:'修改提案',execute:'实验执行',record:'归档结果',done:'结束',baseline_train:'基线训练',baseline_verify:'基线验证',codex:'Codex 修改',check_edits:'检查改动',train:'训练',verify:'独立验证',archive:'结果归档'};
const actions = {experiment:'新实验',repair:'修复',retest:'复测',stop:'停止'};
const num = v => typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('zh-CN') : '—';
const metric = v => typeof v === 'number' && Number.isFinite(v) ? Number(v.toFixed(5)).toString() : '—';
const date = v => typeof v === 'number' && Number.isFinite(v) ? new Date(v * 1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
const pct = v => Math.min(100, Math.max(0, Number(v) || 0));
const tag = status => `<span class="tag ${esc(status)}">${esc(labels[status] || status)}</span>`;
let token = location.hash.slice(1);
try { if (token) sessionStorage.setItem('foreman-token', token); else token = sessionStorage.getItem('foreman-token') || ''; } catch {}
if (location.hash) history.replaceState(null, '', location.pathname);
const state = {view:'connections', overview:null, selected:null, detail:null, tab:'summary', logKind:'controller', retry:false, stats:null, diagnostics:null, loading:false};
function showError(message) { $('#error').textContent = message; $('#error').hidden = false; }
function notice(message) { $('#notice').textContent = message; $('#notice').hidden = false; }
async function api(path, body) {
  const options = {headers:{'X-Foreman-Token':token}, cache:'no-store'};
  if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}
function theme(value) { document.documentElement.dataset.theme = value; $('#theme').innerHTML = icon(value === 'dark' ? 'sun' : 'moon'); try {localStorage.setItem('foreman-theme', value);} catch {} }
let savedTheme; try {savedTheme = localStorage.getItem('foreman-theme');} catch {}
theme(savedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
$('#theme').onclick = () => theme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
$('#refresh').innerHTML = icon('refresh');
function summary() {
  const rows = state.overview?.campaigns || [];
  const cards = [['夜间任务',rows.length,'analytics','blue'],['运行中',rows.filter(r=>['ready','running','paused'].includes(r.status)).length,'refresh','blue'],['等待额度',rows.filter(r=>r.status==='waiting_quota').length,'moon','amber'],['已完成',rows.filter(r=>r.status==='completed').length,'check','green']];
  $('#summary').innerHTML = cards.map(([label,value,name,color])=>`<div class="metaPill metric-${color}"><span class="metricIcon">${icon(name)}</span><div class="metricCopy"><span>${label}</span><strong>${value}</strong></div></div>`).join('');
}
function rows() {
  const query = $('#search').value.toLowerCase(), filter = $('#filter').value;
  const all = state.overview?.campaigns || [];
  const list = all.filter(r => (!query || `${r.goal} ${r.id}`.toLowerCase().includes(query)) && (filter==='all' || (filter==='active' ? !['completed','failed'].includes(r.status) : r.status===filter)));
  $('#task-count').textContent = `${list.length} 个任务`;
  $('#task-rows').innerHTML = list.map(r=>`<tr data-campaign="${esc(r.id)}" class="${r.id===state.selected?'selected':''}"><td><button class="taskName taskSelect" data-campaign="${esc(r.id)}" title="${esc(r.goal)}">${esc(r.goal)}</button><span class="taskId">${esc(r.id)} ${r.needs_resume?' · 待恢复':''}</span></td><td>${tag(r.status)}<div class="phaseText">${esc(phases[r.child_phase] || phases[r.phase] || r.phase)}</div></td><td>${num(r.rounds)} / ${num(r.max_rounds)}<div class="progressTrack"><span style="width:${pct(r.rounds / r.max_rounds * 100)}%"></span></div></td><td><span class="metric">${metric(r.best_metric)}</span></td><td>${date(r.deadline_at)}</td></tr>`).join('');
  $('#empty').hidden = list.length > 0;
  $('#empty h3').textContent = all.length ? '没有匹配的任务' : '安排你的第一个夜间实验';
  $('#empty p').textContent = all.length ? '尝试其他关键词或状态筛选。' : '提交目标后，后台会管理实验与额度等待。已有任务也会自动出现在这里。';
}
async function select(cid) {
  state.selected=cid; state.retry=false; state.detail=null; rows();
  await refreshDetail();
}
async function refreshDetail() {
  if (!state.selected) return;
  const cid=state.selected;
  const detail=await api(`/api/campaigns/${cid}`);
  if (cid!==state.selected) return;
  state.detail=detail; renderDetail();
  if (state.tab==='logs') await loadLogs();
}
function renderDetail() {
  const d=state.detail; if (!d) return;
  const row=state.overview?.campaigns.find(r=>r.id===d.id);
  const complete=d.status==='completed', live=row?.controller_alive;
  const resumeEnabled=!state.actionPending && !complete && !live && (d.status!=='failed' || state.retry);
  $('#detail').innerHTML = `<div class="detailHead"><strong>任务详情</strong>${tag(d.status)}</div><span class="taskId">${esc(d.id)}</span><div class="detailGoal">${esc(d.goal)}</div><div class="detailMeta"><div><span>最佳独立验证指标</span><strong class="metric">${metric(d.best_metric)}</strong></div><div><span>已记录轮数</span><strong>${d.rounds.length} / ${d.max_rounds}</strong></div><div><span>当前阶段</span><strong>${esc(phases[row?.child_phase] || phases[d.phase] || d.phase)}</strong></div><div><span>后台控制器</span><strong>${live?'运行中':complete?'已结束':'未运行'}</strong></div><div><span>截止时间</span><strong>${date(d.deadline_at)}</strong></div><div><span>基线指标</span><strong>${metric(d.initial_metric)}</strong></div></div>${d.quota_wait?`<div class="infoBox waitBox">额度等待 · ${esc(d.quota_wait.scope)}<br>预计恢复：${date(d.quota_wait.wake_at)}<br>等待期间不调用模型</div>`:''}${d.error?`<p class="errorText">${esc(d.error)}</p>`:''}${d.status==='failed'?`<label class="checkLabel"><input id="retry-failed" type="checkbox" ${state.retry?'checked':''}> 已检查错误，允许重试失败阶段</label>`:''}<div class="detailActions"><button data-action="resume" ${resumeEnabled?'':'disabled'}>${live?'已在运行':'恢复任务'}</button><button data-action="stop" class="dangerButton" ${state.actionPending||complete||row?.stop_requested?'disabled':''}>${row?.stop_requested?'已请求停止':'停止后续轮次'}</button>${d.has_archive?'<button data-action="archive">下载最佳归档</button>':''}${d.review_policy?.enabled?'<button data-action="discussion-download">导出审查</button>':''}</div><div class="detailTabs">${[['summary','结果'],['discussion','方案审查'],['logs','日志'],['events','事件'],['report','报告']].map(([id,label])=>`<button data-detail-tab="${id}" class="${state.tab===id?'active':''}">${label}</button>`).join('')}</div><div class="detailContent" id="detail-content"></div>`;
  const content=$('#detail-content');
  if (state.tab==='report') { const pre=document.createElement('pre'); pre.textContent=d.report; content.append(pre); }
  else if (state.tab==='discussion') content.innerHTML=renderDiscussion(d);
  else if (state.tab==='events') content.innerHTML=d.events.map(event=>`<div class="event"><strong>${esc(event.kind)}</strong><small>${date(event.timestamp)}</small></div>`).join('') || '<p>暂无事件</p>';
  else if (state.tab==='logs') { content.innerHTML=`<select class="logSelect" id="log-kind" aria-label="日志来源">${[['controller','调度控制器'],['manager','Codex manager'],['reviewer','独立审查者'],['revision','提案修改'],['worker','Codex worker']].map(([k,l])=>`<option value="${k}" ${k===state.logKind?'selected':''}>${l}</option>`).join('')}</select><pre id="log-output">正在读取日志…</pre>`; }
  else content.innerHTML=`${d.stop_reason?`<div class="infoBox">结束原因：${esc(d.stop_reason)}<br>指标标准：${d.metric_criterion_met===true?'达到':d.metric_criterion_met===false?'未达到':'尚未判断'}</div>`:''}${d.rounds.map(r=>`<div class="round"><div class="roundHeader"><strong>第 ${r.number} 轮 · ${esc(actions[r.action] || r.action)}</strong><span class="metric">${metric(r.metric)}</span></div><p>${esc(r.decision?.rationale || '')}</p>${r.error?`<p class="errorText">${esc(r.error)}</p>`:''}<span class="pillText">${esc(labels[r.status]||r.status)}${r.promoted?' · 更新为最佳结果':''}</span></div>`).join('') || '<p class="muted">尚未记录实验轮次。基线训练、规划和额度等待不计入已完成轮数。</p>'}`;
}
function renderDiscussion(d) {
  if(!d.review_policy?.enabled) return '<p class="muted">此任务未启用自动审查。新建任务时可以开启。</p>';
  const records=[...(d.discussions||[]),...(d.discussion?[d.discussion]:[])];
  if(!records.length) return '<p class="muted">正在准备基线或初始方案，尚无审查记录。</p>';
  const verdicts={approve:'批准执行',revise:'要求修改',reject:'否决'};
  const planNames={hypothesis:'假设',expected_observation:'预期现象',comparison:'对照设计',failure_criterion:'失败标准'};
  const plan=p=>`<div class="discussionPlan">${Object.entries(p.plan||{}).map(([k,v])=>`<p><strong>${esc(planNames[k]||k)}</strong> ${esc(v)}</p>`).join('')}${p.review_response?`<p><strong>提案者回应</strong> ${esc(p.review_response)}</p>`:''}</div>`;
  const issues=r=>(r.issues||[]).length?`<h3>待验证问题</h3><p class="muted">审查者认为已解决不代表实验已验证。</p>${r.issues.map(x=>`<div class="infoBox"><strong>${esc(x.id.slice(0,12))} · ${esc(({open:'未解决',not_raised_latest:'本轮未再提出',reviewer_cleared:'审查者认为已解决'})[x.status]||x.status)}</strong><p>${esc(x.evidence)}</p><p>验证建议：${esc(x.suggestion)}</p><small>出现于审查 ${esc(x.occurrences.join(', '))}</small></div>`).join('')}`:'';
  return `<p class="muted">每个方案最多 ${d.review_policy.max_reviews} 次审查 · 自动决策，无人工审批</p>`+records.map(r=>`<section class="reviewRecord"><h3>实验 ${r.round} · ${esc(r.status)}</h3>${r.reviews.map((entry,i)=>`<div class="round"><div class="roundHeader"><strong>审查 ${i+1}</strong><span class="tag ${entry.review.verdict==='approve'?'completed':'waiting_quota'}">${verdicts[entry.review.verdict]||esc(entry.review.verdict)}</span></div><p>${esc(entry.review.summary)}</p>${plan(entry.proposal)}${entry.review.concerns.map(c=>`<div class="infoBox"><strong>${esc(c.severity)}</strong><br>${esc(c.evidence)}<br>建议：${esc(c.suggestion)}</div>`).join('')}</div>`).join('')}${issues(r)}${r.stall?'<p class="errorText">方案没有实际变化或回到了旧方案，已停止讨论并保留待验证问题。</p>':''}${r.status==='reviewing'?'<h3>当前方案</h3>'+plan(r.proposal):''}</section>`).join('');
}
async function loadLogs() {
  const cid=state.selected, kind=state.logKind;
  const result=await api(`/api/campaigns/${cid}/logs?kind=${kind}`);
  if (state.selected===cid && state.logKind===kind && $('#log-output')) $('#log-output').textContent=result.text;
}
function usageMeter(window, name) {
  const known=window && typeof window.used_percent==='number', used=known?pct(window.used_percent):null;
  const remaining=known?100-used:null, tone=remaining===0?'danger':remaining!==null&&remaining<15?'warning':'normal';
  const duration=window?.duration_minutes;
  const label=duration===300?'5 小时窗口':duration===10080?'每周窗口':duration?`${num(duration)} 分钟窗口`:name==='primary'?'主要窗口':'次要窗口';
  return `<div class="usageMeter tone-${tone}"><div class="usageMeterHead"><span>${label}</span><strong>${known?num(used)+'% 已用':'未知'}</strong></div><div class="usageBar" aria-hidden="true"><span style="width:${used??0}%"></span></div><div class="usageMeterFoot"><span>${remaining===null?'未返回额度数据':num(remaining)+'% 剩余'}</span><span>重置 ${date(window?.resets_at)}</span></div></div>`;
}
function quotaCards() {
  $('#quota-cards').innerHTML=(state.overview?.quota || []).map(q=>`<section class="quotaCard"><div class="quotaHead"><div><h3>${q.role==='manager'?'Manager · 实验规划':q.role==='reviewer'?'Reviewer · 独立审查':'Worker · 代码修改'}</h3><small>${q.plan_type?esc(q.plan_type.toUpperCase()):'PLAN UNKNOWN'}</small></div><span class="tag ${q.stale?'waiting_quota':'completed'}">${!q.available?'暂无数据':q.stale?'缓存已过期':'缓存有效'}</span></div>${['primary','secondary'].map(name=>usageMeter((q.windows||[]).find(w=>w.name===name),name)).join('')}${q.error?`<p class="errorText">刷新未成功：${esc(q.error)}，旧数据保持不变。</p>`:''}<div class="quotaFoot"><span>上次成功读取 ${date(q.fetched_at)}</span><button data-refresh-role="${q.role}">读取最新额度</button></div></section>`).join('');
}
async function analytics() {
  const result=await api('/api/stats'); state.stats=result;
  const u=result.reported_usage, has=(u.jobs_with_usage||0)>0;
  const cards=[['已报告 Token',has?u.total_tokens:null],['输入 Token',has?u.input_tokens:null],['输出 Token',has?u.output_tokens:null],['模型作业数',result.model_jobs]];
  $('#stats-cards').innerHTML=cards.map(([label,value])=>`<div class="statCard"><span>${label}</span><strong>${num(value)}</strong></div>`).join('');
  $('#role-stats').innerHTML=Object.entries(result.by_role).map(([role,v])=>`<div class="roleRow"><div><strong>${role==='manager'?'Manager · 规划':role==='reviewer'?'Reviewer · 审查':'Worker · 修改'}</strong><small>${v.jobs_with_usage||0} / ${v.jobs||0} 个作业报告了 Token · ${v.jobs_with_incomplete_usage||0} 个作业数据不完整</small></div><span>${(v.jobs_with_usage||0)>0?num(v.total_tokens):'未知'} Token</span></div>`).join('')+`<p class="muted">累计已记录额度等待：${num(Math.round(result.quota_wait_seconds))} 秒；并行任务的等待时间可能重叠。</p>`;
}
async function settings() {
  const result=await api('/api/diagnostics'); state.diagnostics=result;
  const names={config:'项目配置',platform:'操作系统',worker_command:'Worker Codex 命令',manager_command:'Manager Codex 命令',reviewer_command:'Reviewer Codex 命令'};
  $('#checks').innerHTML=result.checks.map(c=>`<div class="checkRow"><div><strong>${esc(names[c.check]||c.check)}</strong>${c.hint?`<p>${esc(c.hint)}</p>`:''}</div><span class="tag ${c.ok?'completed':'failed'}">${c.ok?'通过':'需检查'}</span></div>`).join('');
  $('#environment').textContent=`AgentForeman ${result.version} · Python ${result.python_version} · ${result.platform}`;
}
async function navigate(view) {
  state.view=view;
  document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('isActive',b.dataset.view===view);b.setAttribute('aria-pressed',String(b.dataset.view===view));});
  ['connections','tasks','council','usage','analytics','settings'].forEach(v=>$('#view-'+v).hidden=v!==view);
  try { if (view==='connections') await refreshConnections(); if (view==='council') await refreshCouncil(); if (view==='analytics') await analytics(); if (view==='settings') await settings(); } catch(e) {showError(e.message);}
}
async function refresh() {
  if (state.loading) return;
  state.loading=true; $('#refresh').disabled=true;
  try {
    state.overview=await api('/api/overview'); summary(); rows(); quotaCards();
    $('#sync').textContent=`已同步 ${new Date().toLocaleTimeString('zh-CN',{hour12:false})}`;
    document.title=`AgentForeman · ${state.overview.project_name}`;
    if (!state.selected && state.overview.campaigns.length) state.selected=state.overview.campaigns[0].id;
    if (state.selected) {rows(); await refreshDetail();}
    if (state.view==='connections') await refreshConnections();
    if (state.view==='analytics') await analytics();
    if (state.view==='council') await refreshCouncil();
    $('#error').hidden=true;
  } catch(e) {showError(e.message); $('#sync').textContent='连接中断 · 数据可能已过期';}
  finally {state.loading=false; $('#refresh').disabled=false;}
}
function openCreate() { $('#form-error').hidden=true; $('#create-dialog').showModal(); }
$('#new-task').onclick=openCreate; $('#empty-new').onclick=openCreate;
$('#close-dialog').onclick=$('#cancel-create').onclick=()=>$('#create-dialog').close();
$('#create-form').onsubmit=async event=>{
  event.preventDefault(); const button=$('#submit-task'); if (button.disabled) return;
  const form=new FormData(event.currentTarget), rawTarget=form.get('target');
  const deadline=new Date(), [h,m]=String(form.get('deadline')).split(':').map(Number);
  deadline.setHours(h,m,0,0); if (deadline<=new Date()) deadline.setDate(deadline.getDate()+1);
  const body={goal:form.get('goal'),deadline:deadline.toISOString(),max_rounds:Number(form.get('max_rounds')),target:rawTarget===''?null:Number(rawTarget),preflight:form.has('preflight'),review:form.has('review'),max_reviews:Number(form.get('max_reviews'))};
  button.disabled=true; button.textContent='正在提交…'; $('#form-error').hidden=true;
  try { const result=await api('/api/campaigns',body); $('#create-dialog').close(); state.selected=result.id; state.tab='summary'; notice(result.launched?`已提交 ${result.id}。后台实验已启动。`:`任务 ${result.id} 已保存，但控制器未确认启动：${result.error}。请检查详情，不要重复提交。`); await navigate('tasks'); await refresh(); }
  catch(e) {$('#form-error').textContent=`${e.message}。若网络中断，请先刷新任务列表确认是否已创建。`; $('#form-error').hidden=false;}
  finally {button.disabled=false;button.textContent='提交并后台运行';}
};
async function refreshQuota(role) {
  const roles=role?[role]:['manager','reviewer','worker'];
  const buttons=document.querySelectorAll('[data-refresh-role],#refresh-quota'); buttons.forEach(b=>b.disabled=true);
  try { for (const r of roles) { const result=await api('/api/usage',{role:r}); if (result.error) notice(`${r} 额度读取失败：${result.error}，请检查 Codex 登录与版本。`); } await refresh(); }
  catch(e){showError(e.message);} finally{buttons.forEach(b=>b.disabled=false);}
}
$('#refresh-quota').onclick=()=>refreshQuota();
$('#refresh').onclick=refresh; $('#search').oninput=rows; $('#filter').onchange=rows;
$('#home').onclick=()=>navigate('tasks');
async function download(path, filename, jsonData) {
  let blob;
  if (jsonData) blob=new Blob([JSON.stringify(jsonData,null,2)],{type:'application/json'});
  else {const response=await fetch(path,{headers:{'X-Foreman-Token':token},cache:'no-store'});if(!response.ok){const e=await response.json();throw new Error(e.error||'下载失败');}blob=await response.blob();}
  const url=URL.createObjectURL(blob), a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
$('#export-stats').onclick=()=>download(null,'agentforeman-stats.json',state.stats);
$('#export-diagnostics').onclick=()=>download(null,'agentforeman-diagnostics.json',state.diagnostics);
document.addEventListener('change',event=>{if(event.target.id==='retry-failed'){state.retry=event.target.checked;renderDetail();}if(event.target.id==='log-kind'){state.logKind=event.target.value;loadLogs().catch(e=>showError(e.message));}});
document.addEventListener('click',async event=>{
  const b=event.target.closest('button'), row=event.target.closest('[data-campaign]');
  try {
    if (row) return await select(row.dataset.campaign);
    if (!b || b.disabled) return;
    if (b.dataset.view) return await navigate(b.dataset.view);
    if (b.dataset.refreshRole) return await refreshQuota(b.dataset.refreshRole);
    if (b.dataset.detailTab) {state.tab=b.dataset.detailTab;renderDetail();if(state.tab==='logs')await loadLogs();return;}
    if (b.dataset.action) {if(state.actionPending)return;state.actionPending=true;const cid=state.selected, action=b.dataset.action; b.disabled=true;
      if(action==='discussion-download') return await download(null,`${cid}-discussion.json`,{policy:state.detail.review_policy,completed:state.detail.discussions,active:state.detail.discussion});
      if(action==='archive') return await download(`/api/campaigns/${cid}/archive`,`${cid}-best.zip`);
      await api(`/api/campaigns/${cid}/${action}`,action==='resume'?{retry_failed:state.retry}:{});
      notice(action==='resume'?'后台控制器已启动或已在运行。':'已请求停止：完成或协调当前轮后，不再启动下一轮。'); await refresh();
    }
  } catch(e){showError(e.message);} finally{if(b && b.dataset.action){state.actionPending=false;renderDetail();}}
});
async function poll(){if(!document.hidden)await refresh();setTimeout(poll,5000);}
poll();

const councilStatus={running:'运行中',completed_candidate:'候选答案已完成',needs_evidence:'待补证据',rejected:'审查否决',incomplete:'未完成',stopped:'已停止'};
const councilStage={explore:'独立探索',peer_review:'匿名互评',synthesize:'主席综合',audit:'独立审查'};
let councilSelected=null, councilDetail=null, councilMemberKey='';
async function refreshCouncil() {
  const [pool,list,codes]=await Promise.all([api('/api/pool'),api('/api/councils'),api('/api/coding')]);
  $('#coding-list').innerHTML=codes.coding.map(c=>`<button class="councilSelect" data-coding="${esc(c.id)}"><strong>${esc(c.goal)}</strong><small>${esc(c.status)} · ${esc(c.phase)} · 最佳 ${metric(c.best?.metric)}</small></button>`).join('')||'<p class="muted">尚无代码候选实验。</p>';
  if(codingSelected)await selectCoding(codingSelected);
  $('#pool-members').innerHTML=pool.members.map(m=>`<div class="statCard"><strong>${esc(m.id)}</strong><span>${!m.enabled?'已禁用':m.not_before>Date.now()/1000?'额度冷却至 '+date(m.not_before):m.last_seen>Date.now()/1000-60?'在线':'未连接或离线'}</span><small>${esc(m.description)}</small></div>`).join('')||'<p class="muted">尚无节点。先用 pool register 注册成员，再在成员机器上运行 pool worker。</p>';
  const key=pool.members.map(m=>m.id+':'+m.enabled).join(',');
  if(key!==councilMemberKey){councilMemberKey=key;const options=pool.members.filter(m=>m.enabled).map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('');$('#council-members').innerHTML=options;$('#council-chair').innerHTML='<option value="">选择主席节点</option>'+options;}
  $('#council-list').innerHTML=list.councils.map(c=>`<button class="councilSelect" data-council="${esc(c.id)}"><strong>${esc(c.question)}</strong><small>${esc(councilStatus[c.status]||c.status)} · ${esc(councilStage[c.stage]||c.stage)} · ${esc(c.id)}</small></button>`).join('')||'<p class="muted">尚无委员会任务。</p>';
  if(!councilSelected&&list.councils.length)councilSelected=list.councils[0].id;
  if(councilSelected)await selectCouncil(councilSelected);
  await refreshModes(pool.members);
}
async function selectCouncil(cid){
  councilSelected=cid;
  const c=await api('/api/councils/'+cid);if(councilSelected!==cid)return;councilDetail=c;
  $('#start-coding').disabled=!['completed_candidate','needs_evidence'].includes(c.status)||Object.keys(c.protocol_errors||{}).length>0;
  const final=c.stages.synthesize?.[0]?.result,audit=c.stages.audit?.[0]?.result;
  $('#council-detail').innerHTML=`<h3>${esc(c.question)}</h3><p>${esc(councilStatus[c.status]||c.status)} · ${esc(councilStage[c.stage]||c.stage)}</p><p class="muted">截止 ${date(c.deadline)} · 主席 ${esc(c.chair)}</p><div class="detailActions"><button data-council-action="export">导出完整记录</button><button data-council-action="stop" ${c.status==='running'?'':'disabled'}>停止后续阶段</button></div><div class="infoBox">模型共识不等于事实核验。下列实验建议尚未执行。</div>${final?`<h3>主席答案</h3><pre>${esc(final.answer)}</pre><h3>待验证问题</h3><pre>${esc(JSON.stringify({unresolved:final.unresolved,experiments:final.experiments,finding_responses:final.finding_responses},null,2))}</pre>`:'<p>正在等待各节点按阶段提交结果。</p>'}${audit?`<h3>独立审查</h3><pre>${esc(JSON.stringify(audit,null,2))}</pre>`:''}${c.experiment?`<h3>本项目实验</h3><pre>${esc(JSON.stringify(c.experiment,null,2))}</pre><p class="muted">可在实验调度页查看对应任务。项目指标不等于论文结论或新颖性已验证。</p>`:''}<h3>质量检查</h3><pre>${esc(JSON.stringify(c.quality,null,2))}</pre>${['explore','peer_review'].map(stage=>`<details><summary>${councilStage[stage]} · ${(c.stages[stage]||[]).length} 份</summary><pre>${esc(JSON.stringify(c.stages[stage]||[],null,2))}</pre></details>`).join('')}${c.stop_reason?`<p>结束原因：${esc(c.stop_reason)}</p>`:''}`;
}
$('#council-form').onsubmit=async event=>{
  event.preventDefault();const b=$('#submit-council');if(b.disabled)return;b.disabled=true;
  try{const f=new FormData(event.currentTarget);const result=await api('/api/councils',{question:f.get('question'),members:f.getAll('members'),chair:f.get('chair'),sources:JSON.parse(f.get('sources')||'[]'),budget_seconds:Number(f.get('budget_hours'))*3600,stage_seconds:Number(f.get('stage_minutes'))*60,start_at:f.get('start_at')?new Date(f.get('start_at')).getTime()/1000:null,experiment_policy:{enabled:f.has('experiment'),max_rounds:1,budget_seconds:3600}});councilSelected=result.id;notice('委员会任务已保存：'+result.id+'。已连接的 worker 会自动领取。');await refreshCouncil();}
  catch(e){showError(e.message);}finally{b.disabled=false;}
};
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b||b.disabled)return;
  try{if(b.dataset.council)await selectCouncil(b.dataset.council);if(b.dataset.councilAction==='export')await download(null,'council-'+councilSelected+'.json',councilDetail);if(b.dataset.councilAction==='stop'){await api('/api/councils/'+councilSelected+'/stop',{});await refreshCouncil();}}
  catch(e){showError(e.message);}
});

let codingSelected=null,codingDetail=null;
async function selectCoding(cid){
  codingSelected=cid;const c=await api('/api/coding/'+cid);if(codingSelected!==cid)return;codingDetail=c;
  $('#coding-detail').innerHTML=`<h3>代码候选 ${esc(cid)}</h3><p>${esc(c.status)} · ${esc(c.phase)} · 基线 ${metric(c.baseline?.metric)} · 最佳 ${metric(c.best?.metric)}</p><div class="detailActions"><button data-code-action="export">导出代码候选记录</button>${c.best?'<button data-code-action="archive">下载最佳代码归档</button>':''}<button data-code-action="stop" ${c.status==='running'?'':'disabled'}>停止后续候选</button></div>${c.candidates.map(x=>`<div class="infoBox"><strong>${esc(x.member)} · ${esc(x.status)} · ${metric(x.metric)}</strong><p>${esc(x.answer.summary)}</p><p>${esc(x.reason||'')}</p></div>`).join('')}<p class="muted">独立验证的项目指标不等于论文新颖性或广泛事实已验证。</p>`;
}
$('#start-coding').onclick=async()=>{
  const b=$('#start-coding');b.disabled=true;
  try{const result=await api('/api/coding',{goal:'',members:councilDetail.members,council_id:councilDetail.id,budget_seconds:3600,proposal_seconds:600});codingSelected=result.id;notice('代码候选任务已保存：'+result.id);await refreshCouncil();}
  catch(e){showError(e.message);}finally{b.disabled=!councilDetail||!['completed_candidate','needs_evidence'].includes(councilDetail.status);}
};
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b||b.disabled)return;
  try{if(b.dataset.coding)await selectCoding(b.dataset.coding);if(b.dataset.codeAction==='export')await download(null,'coding-'+codingSelected+'.json',codingDetail);if(b.dataset.codeAction==='archive')await download('/api/coding/'+codingSelected+'/archive',codingSelected+'-code.zip');if(b.dataset.codeAction==='stop'){await api('/api/coding/'+codingSelected+'/stop',{});await selectCoding(codingSelected);}}
  catch(e){showError(e.message);}
});

const modeStatus={running:'运行中',completed_scoped:'配置的检查范围内通过',needs_evidence:'待补证据',waiting_clarification:'等待需求澄清'};
$('#open-modes').onclick=()=>$('#modes-workspace').scrollIntoView({block:'start'});
const modeStage={plan:'任务拆分',contribute:'并行贡献',propose:'提案 / 集成',verify:'独立验证',critique:'可核查批评',revise:'修改产物',chair:'主席综合',done:'结束'};
let modeSelected=null,modeDetail=null,modeMemberKey='';
$('#mode-kind').onchange=()=>{
  const cooperative=$('#mode-kind').value==='cooperations';
  $('#mode-contributors-field').hidden=!cooperative;
  $('#mode-contributors').required=cooperative;
};
async function refreshModes(members){
  const key=members.map(m=>m.id+':'+m.enabled).join(',');
  if(key!==modeMemberKey){
    modeMemberKey=key;
    const options=members.filter(m=>m.enabled).map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('');
    for(const id of ['author','reviewers','contributors','chair'])$('#mode-'+id).innerHTML=(['author','chair'].includes(id)?'<option value="">选择节点</option>':'')+options;
  }
  const [debates,cooperations]=await Promise.all([api('/api/debates'),api('/api/cooperations')]);
  const rows=[...debates.tasks.map(t=>({...t,route:'debates'})),...cooperations.tasks.map(t=>({...t,route:'cooperations'}))].sort((a,b)=>b.created_at-a.created_at);
  $('#mode-list').innerHTML=rows.map(t=>`<button class="councilSelect" data-mode-route="${t.route}" data-mode-id="${esc(t.id)}"><strong>${esc(t.question)}</strong><small>${t.route==='debates'?'对抗':'合作'} · ${esc(modeStatus[t.status]||t.status)} · ${esc(modeStage[t.stage]||t.stage)} · ${t.round}/${t.max_rounds}</small></button>`).join('')||'<p class="muted">尚无合作或对抗任务。</p>';
  if(!modeSelected&&rows.length)modeSelected={route:rows[0].route,id:rows[0].id};
  if(modeSelected)await selectMode(modeSelected.route,modeSelected.id);
  await refreshBriefings();
}
async function selectMode(route,id){
  modeSelected={route,id};const c=await api(`/api/${route}/${id}`);
  if(modeSelected.route!==route||modeSelected.id!==id)return;
  modeDetail=c;
  const selected=c.versions.find(v=>v.id===c.selected_version)||c.versions.at(-1);
  const answer=selected?.proposal.artifacts.find(a=>a.id==='answer')?.content;
  const json=value=>`<pre>${esc(JSON.stringify(value,null,2))}</pre>`;
  $('#mode-detail').innerHTML=`${c.task_contract?'<div class="infoBox">已绑定人类确认的任务约定 v'+esc(c.task_contract.version)+' · '+esc(c.task_contract.sha256.slice(0,12))+'<br><button data-briefing="'+esc(c.task_contract.briefing_id)+'">查看原始澄清与约定</button></div>':''}<h3>${esc(c.question)}</h3><p>${route==='debates'?'对抗模式':'合作模式'} · ${esc(modeStatus[c.status]||c.status)} · ${esc(modeStage[c.stage]||c.stage)} · 第 ${c.round}/${c.max_rounds} 轮</p><p class="taskId">${esc(id)} · 截止 ${date(c.deadline)}</p><div class="detailActions"><button data-mode-action="export">导出完整记录</button><button data-mode-action="report">下载报告</button><button data-mode-action="stop" ${c.status==='running'&&!c.stop_requested?'':'disabled'}>${c.stop_requested?'已请求停止':'停止后续阶段'}</button></div><div class="infoBox">主席意见和模型共识不等于事实。检查仅验收其配置范围；未解决的批评不会因模型同意而消失。运行需保持 pool serve 与 worker 在线，重启后从保存状态继续。</div>${answer!==undefined?'<h3>当前选择的产物</h3><pre>'+esc(answer)+'</pre>':'<p>等待产物。</p>'}<h3>独立复测</h3>${json(selected?.checks||[])}<h3>待验证问题</h3>${json(c.pending_questions||c.issues.filter(i=>i.status!=='check_passed_scoped'))}<h3>批评与回应记录</h3>${json(c.reviews)}${c.plan?'<h3>拆分与集成约定</h3>'+json(c.plan):''}${c.contributions?'<h3>各成员贡献（尚非独立验收结论）</h3>'+json(c.contributions):''}${c.contribution_tasks?'<details><summary>子任务状态与部分结果</summary>'+json(c.contribution_tasks)+'</details>':''}<h3>修改与验证进展</h3>${json(c.progress)}<details><summary>全部版本与产物</summary>${json(c.versions)}</details><h3>主席意见（仅供综合）</h3>${json(c.chair_summary||null)}<h3>质量与协议错误</h3>${json({quality:c.quality,errors:c.protocol_errors})}${c.stop_reason?'<p>停止原因：'+esc(c.stop_reason)+'</p>':''}`;
}
$('#mode-form').onsubmit=async event=>{
  event.preventDefault();const button=$('#submit-mode');if(button.disabled)return;button.disabled=true;
  try{
    const f=new FormData(event.currentTarget),route=f.get('mode');
    const author=f.get('author'),reviewers=f.getAll('reviewers'),contributors=f.getAll('contributors');
    const roles=[author,...reviewers,...(route==='cooperations'?contributors:[]),f.get('chair')];
    if(new Set(roles).size!==roles.length)throw new Error('提案者、贡献者、审查者和主席必须使用不同节点');
    const body={question:f.get('question'),members:[author,...(route==='cooperations'?contributors:reviewers)],chair:f.get('chair'),evidence:JSON.parse(f.get('evidence')||'[]'),checks:JSON.parse(f.get('checks')||'[]'),max_rounds:Number(f.get('max_rounds')),budget_seconds:Number(f.get('budget_hours'))*3600,stage_seconds:Number(f.get('stage_minutes'))*60};
    if(route==='cooperations')body.reviewers=reviewers;
    if(f.has('brief_first')){
      const execution={mode:route==='debates'?'adversarial':'cooperative',members:body.members,chair:body.chair,max_rounds:body.max_rounds,budget_seconds:body.budget_seconds,stage_seconds:body.stage_seconds};
      if(body.reviewers)execution.reviewers=body.reviewers;
      const result=await api('/api/briefings',{idea:body.question,execution,evidence:body.evidence,checks:body.checks});
      briefingSelected=result.id;briefingRenderKey='';notice('已开始需求澄清。确认前只派发主席任务，不启动合作 / 对抗执行。');
      await refreshCouncil();$('#briefing-workspace').scrollIntoView({block:'start'});
    }else{
      const result=await api('/api/'+route,body);modeSelected={route,id:result.id};
      notice('任务已保存：'+result.id+'。请保持 pool serve 和各角色 worker 在线。');await refreshCouncil();
    }
  }catch(e){showError(e.message+'。如网络中断，请先刷新列表确认是否已创建。');}finally{button.disabled=false;}
};
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b||b.disabled)return;
  try{
    if(b.dataset.modeId)return await selectMode(b.dataset.modeRoute,b.dataset.modeId);
    if(!b.dataset.modeAction||!modeSelected)return;
    const {route,id}=modeSelected;
    if(b.dataset.modeAction==='export')return await download(null,id+'-transcript.json',modeDetail);
    if(b.dataset.modeAction==='report')return await download(`/api/${route}/${id}/report`,id+'-report.md');
    b.disabled=true;await api(`/api/${route}/${id}/stop`,{});await selectMode(route,id);
  }catch(e){showError(e.message);}
});

const briefingStatus={running:'主席整理中',waiting_human:'等待你补充',ready:'待你确认开工',launched:'已启动执行',stopped:'已停止澄清'};
let briefingSelected=null,briefingDetail=null,briefingRenderKey='',briefingBusy=false,briefingDirty=false;
$('#brief-first').onchange=()=>$('#submit-mode').textContent=$('#brief-first').checked?'先与主席澄清需求':'直接启动合作 / 对抗任务';
async function refreshBriefings(){
  const result=await api('/api/briefings');
  $('#briefing-list').innerHTML=result.briefings.map(c=>`<button class="councilSelect" data-briefing="${esc(c.id)}"><strong>${esc(c.idea)}</strong><small>${esc(briefingStatus[c.status]||c.status)} · 约定 v${c.version} · 澄清 ${c.turn} 轮</small></button>`).join('')||'<p class="muted">新任务默认先与主席澄清，也可在表单中选择直接执行。</p>';
  if(!briefingSelected&&result.briefings.length)briefingSelected=result.briefings[0].id;
  if(briefingSelected)await selectBriefing(briefingSelected);
}
function contractView(c){
  const items=values=>'<ul>'+values.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul>';
  return `<h4>目标</h4><p>${esc(c.goal)}</p><h4>输入与条件</h4>${items(c.inputs)}<h4>交付物</h4>${items(c.deliverables.map(d=>d.id+'：'+d.description))}<h4>验收标准</h4>${items(c.acceptance.map(a=>(a.verification==='automated'?'程序检查 '+a.check_ids.join(', ')+'：':'仍需人工验收：')+a.criterion))}<h4>约束</h4>${items(c.constraints)}<h4>优先级</h4>${items(c.priorities)}<h4>不做的事情</h4>${items(c.exclusions)}<h4>可以自主决定</h4>${items(c.autonomy.allowed_decisions)}<h4>需要暂停澄清</h4>${items(c.autonomy.pause_conditions)}<h4>约定的默认处理</h4>${items(c.autonomy.defaults)}`;
}
async function selectBriefing(id){
  briefingSelected=id;const c=await api('/api/briefings/'+id);if(briefingSelected!==id)return;
  const key=JSON.stringify([id,c.status,c.version,c.turn,c.messages.length,c.errors.length,c.launch]);
  briefingDetail=c;if(briefingRenderKey===key)return;briefingRenderKey=key;briefingDirty=false;
  const ready=c.status==='ready',interactive=['waiting_human','ready'].includes(c.status);
  $('#briefing-detail').innerHTML=`<h3>先把任务谈清楚</h3><p>${esc(briefingStatus[c.status]||c.status)} · 约定 v${c.version} · 澄清 ${c.turn}/${c.max_turns} 轮</p><div class="infoBox">此处确认的是目标与执行边界，不是结论正确。执行预算从正式启动时计算；等待你回复不会继续调用模型。自动检查由你事先配置，主席不能生成执行命令。</div><div class="briefMessages">${c.messages.map(m=>`<article class="infoBox"><strong>${m.role==='human'?'你':'Chairman'}</strong><p>${esc(m.content)}</p>${[...(m.questions||[]),...(m.open_questions||[])].map(q=>'<p>待澄清：'+esc(q)+'</p>').join('')}</article>`).join('')}</div>${interactive?`<form id="brief-reply-form"><label class="field">补充需求 / 回答问题<textarea id="brief-reply" required maxlength="8000" rows="3"></textarea></label><button type="submit" id="brief-send" ${c.turn>=c.max_turns?'disabled':''}>发给主席继续澄清</button></form>`:''}${c.draft?`<section class="contractPreview"><h3>任务约定 v${c.version}</h3>${contractView(c.draft.contract)}</section>`:''}${c.draft?.role_plan?'<h4>主席分工理由</h4><p>'+esc(c.draft.role_plan.rationale)+'</p>':''}<h4>执行配置</h4><p>${c.execution.mode==='cooperative'?'合作':'对抗'} · 成员 ${esc(c.execution.members.join(', '))} · 主席 ${esc(c.execution.chair)} · ${c.execution.budget_seconds/3600} 小时 · 最多 ${c.execution.max_rounds} 轮</p><details><summary>查看执行参数</summary><pre>${esc(JSON.stringify(c.execution,null,2))}</pre></details>${ready?`<form id="brief-edit-form"><label class="field">编辑目标<textarea id="brief-goal" rows="2" required maxlength="6000">${esc(c.draft.contract.goal)}</textarea></label><details><summary>编辑完整约定（JSON）</summary><label class="field">任务约定<textarea id="brief-contract-json" rows="12" required>${esc(JSON.stringify(c.draft.contract,null,2))}</textarea></label></details><details><summary>编辑节点与预算（JSON）</summary><label class="field">执行参数<textarea id="brief-execution-json" rows="8" required>${esc(JSON.stringify(c.execution,null,2))}</textarea></label></details><button type="submit" id="brief-save">保存为新版本</button></form><p class="muted" id="brief-dirty-note">确认将绑定当前展示的约定、资料、检查器、节点和预算。</p><button class="primary" id="brief-confirm">确认 v${c.version} 并正式开工</button>`:''}${c.status==='running'&&c.stage==='launch'?'<p>已保存启动意图，后台将恢复同一个执行任务。请勿重复创建任务。</p>':''}${c.launch?`<p>执行任务：${esc(c.launch.id)}</p>${c.status==='launched'?`<button data-brief-open="${esc(c.launch.id)}" data-brief-route="${c.launch.mode==='adversarial'?'debates':'cooperations'}">查看执行进度</button><p class="muted">已启动的约定保持冻结。调整目标请另建澄清任务，先停止或协调原任务。</p>`:''}`:''}${c.errors.length?'<h4>需要处理</h4><pre>'+esc(JSON.stringify(c.errors,null,2))+'</pre>':''}<button data-brief-export="${esc(id)}">导出澄清与约定记录</button>${!(["launched","stopped"].includes(c.status))&&c.stage!=="launch"?'<button data-brief-stop="'+esc(id)+'">停止澄清</button>':''}`;
}
document.addEventListener('input',event=>{
  if(['brief-goal','brief-contract-json','brief-execution-json'].includes(event.target.id)){
    briefingDirty=true;if($('#brief-confirm'))$('#brief-confirm').disabled=true;
    $('#brief-dirty-note').textContent='有未保存的修改。请先保存新版本，再核对并确认。';
  }
});
document.addEventListener('submit',async event=>{
  if(!['brief-reply-form','brief-edit-form'].includes(event.target.id))return;
  event.preventDefault();if(briefingBusy)return;briefingBusy=true;
  const c=briefingDetail;
  try{
    if(event.target.id==='brief-reply-form'){
      await api(`/api/briefings/${c.id}/message`,{version:c.version,content:$('#brief-reply').value});
    }else{
      const contract=JSON.parse($('#brief-contract-json').value);
      if($('#brief-goal').value!==c.draft.contract.goal)contract.goal=$('#brief-goal').value;
      await api(`/api/briefings/${c.id}/edit`,{version:c.version,contract,execution:JSON.parse($('#brief-execution-json').value)});
    }
    briefingRenderKey='';await refreshBriefings();
  }catch(e){showError(e.message);}finally{briefingBusy=false;}
});
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b||b.disabled)return;
  try{
    if(b.dataset.briefing){briefingRenderKey='';return await selectBriefing(b.dataset.briefing);}
    if(b.dataset.briefExport)return await download(null,b.dataset.briefExport+'-briefing.json',briefingDetail);
    if(b.dataset.briefStop){await api('/api/briefings/'+b.dataset.briefStop+'/stop',{});briefingRenderKey='';return await refreshBriefings();}
    if(b.dataset.briefOpen){await selectMode(b.dataset.briefRoute,b.dataset.briefOpen);$('#mode-detail').scrollIntoView({block:'start'});return;}
    if(b.id!=='brief-confirm'||briefingBusy||briefingDirty)return;
    briefingBusy=true;b.disabled=true;const c=briefingDetail;
    const result=await api(`/api/briefings/${c.id}/confirm`,{version:c.version,sha256:c.sha256});
    modeSelected={route:result.launch.mode==='adversarial'?'debates':'cooperations',id:result.launch.id};
    briefingRenderKey='';notice('已确认并启动。可以离开页面，保持协调器和 worker 运行。');await refreshCouncil();
  }catch(e){showError(e.message);if(b.id==='brief-confirm')b.disabled=false;}finally{if(b.id==='brief-confirm')briefingBusy=false;}
});

let connectionData=null,connectionChoiceKey='';
function connectionGuide(){
  const provider=$('#connection-provider').value,isAPI=['anthropic','openai-compatible'].includes(provider);
  $('#connection-api-fields').hidden=!isAPI;$('#connection-url-field').hidden=false;
  const guide=connectionData?.guides[provider],client=connectionData?.clients.find(c=>c.provider===provider);
  $('#connection-guide').innerHTML=isAPI?'填写服务商实际提供的地址与模型名。添加后会发送一次有界小请求测试，可能消耗少量 API 额度。':`<strong>${client?.installed?'已检测到客户端；登录状态需实际测试。':'尚未检测到客户端。'}</strong><p>安装：<code>${esc(guide?.install||'请安装对应官方客户端')}</code></p><p>先在本机终端完成官方登录：<code>${esc(guide?.login||provider)}</code>。已登录可直接添加测试；账号令牌由客户端管理。</p><button type="button" data-connection-login="${esc(provider)}" ${client?.installed?'':'disabled'}>打开官方登录终端</button>`;
}
$('#connection-provider').onchange=connectionGuide;
async function refreshConnections(){
  connectionData=await api('/api/connections');
  $('#connection-service').textContent=connectionData.error||'本机协调服务已启动。测试通过后自动运行节点，无需手动配置 worker。';
  const statuses={passed:'测试通过',failed:'测试失败',busy:'资源忙，请稍后重试',waiting_quota:'等待额度',blocked:'尚未连接'};
  if(!document.activeElement?.closest('.connectionKeyForm'))$('#connection-cards').innerHTML=connectionData.connections.map(c=>`<article class="statCard"><strong>${esc(c.label)}</strong><span>${esc(c.provider)} · ${esc(c.model||'默认模型')}</span><small>${c.testing?'测试中…':!c.key_available?'需要重新提供 API 密钥':c.retry_at>Date.now()/1000?'额度等待至 '+date(c.retry_at):!c.enabled?(statuses[c.test?.status]||'尚未测试')+' · 未启用':!c.active_now?'等待可用时段':c.worker_running?'已启用 · 后台等待任务':'后台节点未运行'}</small><small>共享资源：${esc(c.resource_group)}${c.secret_storage?' · 密钥：'+esc(c.secret_storage==='os'?'系统凭据库':c.secret_storage==='session'?'仅本次进程':'环境变量'):''}</small>${c.test?'<small>'+esc(c.test.diagnostic)+'</small>':''}${!['codex','claude-code','gemini-cli','opencode'].includes(c.provider)?`<details><summary>更新 API 密钥</summary><form class="connectionKeyForm" data-connection-key="${c.id}"><label class="field">更新密钥（仅本次进程）<input type="password" name="api_key" required autocomplete="new-password" maxlength="8192"></label><button type="submit">更新后重新测试</button></form></details>`:''}<div class="detailActions"><button data-connection-test="${c.id}" ${c.testing?'disabled':''}>${c.test?'重新测试':'测试并启用'}</button><button data-connection-enable="${c.id}" data-enabled="${!c.enabled}" ${!c.test?.passed?'disabled':''}>${c.enabled?'暂停领取新任务':'启用'}</button></div></article>`).join('')||'<p class="muted">添加你的第一个本机账号或 API 连接。</p>';
  const choices=connectionData.connections.filter(c=>c.enabled&&c.test?.passed&&c.key_available&&c.worker_running);
  const key=JSON.stringify(choices.map(c=>[c.member,c.label]));
  if(key!==connectionChoiceKey){const selected=$('#quick-chair').value;connectionChoiceKey=key;$('#quick-chair').innerHTML='<option value="">自动选择（优先使用你标记的主席）</option>'+choices.map(c=>`<option value="${esc(c.member)}">${esc(c.label)} · ${esc(c.model||c.provider)}</option>`).join('');if(choices.some(c=>c.member===selected))$('#quick-chair').value=selected;}
  connectionGuide();
}
$('#connection-form').onsubmit=async event=>{
  event.preventDefault();const form=event.currentTarget,b=$('#connection-add');if(b.disabled)return;b.disabled=true;
  try{
    const f=new FormData(form),provider=f.get('provider');
    const body={label:f.get('label'),provider,model:f.get('model'),active_hours:f.get('active_hours'),chair_preferred:f.has('chair_preferred')};
    if(['anthropic','openai-compatible'].includes(provider))Object.assign(body,{api_key:f.get('api_key'),key_env:f.get('key_env'),remember:f.has('remember'),base_url:f.get('base_url')||(provider==='anthropic'?'https://api.anthropic.com/v1':'')});
    const c=await api('/api/connections',body);
    form.elements.api_key.value='';
    await api(`/api/connections/${c.id}/test`,{});
    notice('连接已保存，正在执行真实连接测试；通过后自动加入调度。');await refreshConnections();
  }catch(e){showError(e.message);}finally{b.disabled=false;}
};
document.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b||b.disabled)return;
  try{
    if(b.dataset.connectionLogin){await api('/api/connections/login',{provider:b.dataset.connectionLogin});notice('请在新终端完成官方登录，然后添加 / 测试连接。');}
    if(b.dataset.connectionTest){b.disabled=true;await api(`/api/connections/${b.dataset.connectionTest}/test`,{});await refreshConnections();}
    if(b.dataset.connectionEnable){b.disabled=true;await api(`/api/connections/${b.dataset.connectionEnable}/enable`,{enabled:b.dataset.enabled==='true'});await refreshConnections();}
  }catch(e){showError(e.message);b.disabled=false;}
});
$('#quick-task-form').onsubmit=async event=>{
  event.preventDefault();const b=$('#quick-task-submit');if(b.disabled)return;b.disabled=true;
  try{
    const f=new FormData(event.currentTarget),body={idea:f.get('idea'),mode:f.get('mode'),evidence:JSON.parse(f.get('evidence')||'[]'),checks:JSON.parse(f.get('checks')||'[]')};
    if(f.get('chair'))body.chair=f.get('chair');
    const result=await api('/api/quick-tasks',body);briefingSelected=result.id;briefingRenderKey='';
    notice('已交给主席澄清需求并分配角色。确认任务约定前不会正式开工。');
    await navigate('council');$('#briefing-workspace').scrollIntoView({block:'start'});
  }catch(e){showError(e.message+'。网络中断时请先查看澄清任务列表，避免重复提交。');}finally{b.disabled=false;}
};

document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.dataset.connectionKey)return;event.preventDefault();
  const b=form.querySelector('button');if(b.disabled)return;b.disabled=true;
  try{
    await api(`/api/connections/${form.dataset.connectionKey}/key`,{api_key:form.elements.api_key.value});
    form.elements.api_key.value='';await api(`/api/connections/${form.dataset.connectionKey}/test`,{});
    notice('密钥已更新，正在重新测试。');await refreshConnections();
  }catch(e){showError(e.message);}finally{b.disabled=false;}
});
