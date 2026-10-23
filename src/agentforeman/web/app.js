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
const state = {view:'tasks', overview:null, selected:null, detail:null, tab:'summary', logKind:'controller', retry:false, stats:null, diagnostics:null, loading:false};
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
  const resumeEnabled=!complete && !live && (d.status!=='failed' || state.retry);
  $('#detail').innerHTML = `<div class="detailHead"><strong>任务详情</strong>${tag(d.status)}</div><span class="taskId">${esc(d.id)}</span><div class="detailGoal">${esc(d.goal)}</div><div class="detailMeta"><div><span>最佳独立验证指标</span><strong class="metric">${metric(d.best_metric)}</strong></div><div><span>已记录轮数</span><strong>${d.rounds.length} / ${d.max_rounds}</strong></div><div><span>当前阶段</span><strong>${esc(phases[row?.child_phase] || phases[d.phase] || d.phase)}</strong></div><div><span>后台控制器</span><strong>${live?'运行中':complete?'已结束':'未运行'}</strong></div><div><span>截止时间</span><strong>${date(d.deadline_at)}</strong></div><div><span>基线指标</span><strong>${metric(d.initial_metric)}</strong></div></div>${d.quota_wait?`<div class="infoBox waitBox">额度等待 · ${esc(d.quota_wait.scope)}<br>预计恢复：${date(d.quota_wait.wake_at)}<br>等待期间不调用模型</div>`:''}${d.error?`<p class="errorText">${esc(d.error)}</p>`:''}${d.status==='failed'?`<label class="checkLabel"><input id="retry-failed" type="checkbox" ${state.retry?'checked':''}> 已检查错误，允许重试失败阶段</label>`:''}<div class="detailActions"><button data-action="resume" ${resumeEnabled?'':'disabled'}>${live?'已在运行':'恢复任务'}</button><button data-action="stop" class="dangerButton" ${complete||row?.stop_requested?'disabled':''}>${row?.stop_requested?'已请求停止':'停止后续轮次'}</button>${d.has_archive?'<button data-action="archive">下载最佳归档</button>':''}${d.review_policy?.enabled?'<button data-action="discussion-download">导出审查</button>':''}</div><div class="detailTabs">${[['summary','结果'],['discussion','方案审查'],['logs','日志'],['events','事件'],['report','报告']].map(([id,label])=>`<button data-detail-tab="${id}" class="${state.tab===id?'active':''}">${label}</button>`).join('')}</div><div class="detailContent" id="detail-content"></div>`;
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
  ['tasks','usage','analytics','settings'].forEach(v=>$('#view-'+v).hidden=v!==view);
  try { if (view==='analytics') await analytics(); if (view==='settings') await settings(); } catch(e) {showError(e.message);}
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
    if (state.view==='analytics') await analytics();
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
    if (b.dataset.action) {const cid=state.selected, action=b.dataset.action; b.disabled=true;
      if(action==='discussion-download') return await download(null,`${cid}-discussion.json`,{policy:state.detail.review_policy,completed:state.detail.discussions,active:state.detail.discussion});
      if(action==='archive') return await download(`/api/campaigns/${cid}/archive`,`${cid}-best.zip`);
      await api(`/api/campaigns/${cid}/${action}`,action==='resume'?{retry_failed:state.retry}:{});
      notice(action==='resume'?'后台控制器已启动或已在运行。':'已请求停止：完成或协调当前轮后，不再启动下一轮。'); await refresh();
    }
  } catch(e){showError(e.message);} finally{if(b && b.isConnected && b.dataset.action)renderDetail();}
});
async function poll(){if(!document.hidden)await refresh();setTimeout(poll,5000);}
poll();
