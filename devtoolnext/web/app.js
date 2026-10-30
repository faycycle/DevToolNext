const $=id=>document.getElementById(id);
const transcript=globalThis.DevToolTranscript;
let token=new URLSearchParams(location.hash.slice(1)).get('token')||sessionStorage.getItem('devtoolnext-token')||'';
history.replaceState(null,'',location.pathname);
let sid='',cursor=0,records=[],sessions=[],status=null,role='',reachable=false,busy=false,paused=false,pauseCount=0,mode='log',selected=null,selectionVersion=0,tickCount=0;
let sessionSignature='',activeConfigKey='',noticeSource='',presets=[],presetSignature='',settings={style:'studio',theme:'dark'};
try{const saved=JSON.parse(localStorage.getItem('devtoolnext-appearance')||'{}');settings={...settings,...saved}}catch{}
const styleDefaults={studio:'dark',graphite:'dark',lab:'light',paper:'light',midnight:'dark',terminal:'dark'};
function appearance(style,theme){
 if(!Object.hasOwn(styleDefaults,style))style='studio';
 if(!['dark','light'].includes(theme))theme=styleDefaults[style];
 settings={style,theme};document.documentElement.dataset.style=style;document.documentElement.dataset.theme=theme;
 $('style').value=style;$('theme').textContent=theme==='dark'?'浅色':'深色';
 try{localStorage.setItem('devtoolnext-appearance',JSON.stringify(settings))}catch{}
}
function notice(message,fromService=false){$('notice').textContent=message;$('notice').hidden=!message;noticeSource=message&&fromService?'service':''}
function currentSession(){return sessions.find(s=>s.id===sid)}
function liveSelected(){return Boolean(status?.state==='connected'&&status.session_id===sid)}
function controls(){
 const canControl=role==='control'&&reachable&&!busy;
 $('connect').disabled=!canControl||status?.state==='connected';$('open-connect').disabled=!canControl||status?.state==='connected';
 $('config').disabled=!reachable||!status?.config;
 $('disconnect').disabled=!canControl||!['connected','fault'].includes(status?.state);
 $('send').disabled=!canControl||!liveSelected();$('payload').disabled=!canControl||!liveSelected();
 $('export').disabled=!sid||!reachable||busy;
 $('input-hint').textContent=!reachable?'请先认证并连接设备服务。':role==='view'?'只读访问：可查看、搜索和导出，不能发送或断开设备。':!liveSelected()?(sid?'当前会话未在采集，不能向历史记录发送。':'连接设备后可发送。'):'发送成功表示驱动接受字节，不代表设备命令完成。';
 $('permission-title').textContent=role==='control'?'访问权限：控制':role==='view'?'访问权限：只读':'访问权限：未认证';
 $('permission-note').textContent='本阶段提供控制与只读权限；AI/MCP 控制和多人控制权尚未接入。';
 $('refresh').disabled=!reachable||busy;$('login').disabled=busy;
}
async function api(path,body,method){
 const response=await fetch('/api'+path,{method:method||(body===undefined?'GET':'POST'),headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(8000)});
 if(!response.ok){const error=await response.json().catch(()=>({}));if(response.status===401){role='';reachable=false;$('auth').hidden=false;controls()}throw Error(typeof error.detail==='string'?error.detail:'请求失败：'+response.status)}
 return response.json();
}
function setSidebar(open){document.querySelector('.shell').classList.toggle('devices-open',open);$('sidebar-backdrop').hidden=!open;$('devices-toggle').setAttribute('aria-expanded',String(open))}
function showDetails(open=true){$('content').classList.toggle('inspect-hidden',!open);$('content').classList.toggle('show-overlay',open&&innerWidth<=960);$('details').setAttribute('aria-expanded',String(open));applyLayout()}
function updateContext(){
 const session=currentSession(),config=session?.config||(sid===status?.session_id?status?.config:null);
 $('device-title').textContent=config?`串口 · ${config.port}`:'设备工作台';
 $('session-label').textContent=sid?sid.slice(0,12).toUpperCase():'NO SESSION';
 $('context-status').textContent=liveSelected()?'已连接 · 原始录制中 · '+(role==='control'?'控制权限':'只读观察'):session?`${session.error?'不完整会话':'历史会话'} · ${new Date(session.started*1000).toLocaleString('zh-CN')} · 只读回看`:'连接串口，或打开已有会话。';
 $('send-target').textContent=liveSelected()?status.config.port:'未连接';
 $('device-name').textContent=status?.config?.port||'暂无连接';
 $('device-config').textContent=status?.config?`${status.config.baudrate} · ${status.config.bytesize}${status.config.parity}${status.config.stopbits} · ${status.state==='connected'?'已连接':'已断开'}`:'连接设备开始采集';
 $('active-device').classList.toggle('selected',Boolean(sid&&sid===status?.session_id));
 $('config').textContent=status?.config?`${status.config.baudrate} · ${status.config.bytesize}${status.config.parity}${status.config.stopbits}`:'连接参数';
}
function renderSessionList(){
 const list=$('session-list'),q=$('session-search').value.toLowerCase();list.replaceChildren();
 const matches=sessions.filter(s=>(s.config.port+' '+new Date(s.started*1000).toLocaleString('zh-CN')+' '+s.id).toLowerCase().includes(q));
 for(const session of matches){const button=document.createElement('button');button.className='history-item'+(session.id===sid?' selected':'');button.dataset.session=session.id;button.setAttribute('aria-current',session.id===sid?'true':'false');
 const title=document.createElement('span');title.textContent=session.config.port+' · '+(session.error?'不完整':!session.ended?'录制中':'已保存');
 const time=document.createElement('small');time.textContent=new Date(session.started*1000).toLocaleString('zh-CN');button.append(title,time);button.onclick=()=>{selectSession(session.id);setSidebar(false)};list.append(button)}
 if(!matches.length){const empty=document.createElement('p');empty.className='sidebar-empty';empty.textContent=q?'未找到匹配会话':'暂无录制会话';list.append(empty)}
 $('session-count').textContent=String(sessions.length);
}
function formConfig(){return {port:$('port').value.trim(),baudrate:Number($('baud').value),bytesize:Number($('bits').value),parity:$('parity').value,stopbits:Number($('stop').value),rtscts:$('flow').value==='true'}}
function renderPresets(){
 const list=$('preset-list');list.replaceChildren();
 const items=presets.slice(0,12);
 if(!items.length){const empty=document.createElement('p');empty.className='sidebar-empty';empty.textContent='连接一次后会自动记住参数。';list.append(empty)}
 for(const preset of items){
  const row=document.createElement('div');row.className='preset-item';
  const config=preset.config||{};
  if(status?.config&&config.port===status.config.port&&config.baudrate===status.config.baudrate)row.classList.add('active');
  const main=document.createElement('button');main.type='button';main.className='preset-main';main.title='填入连接设置';
  const name=document.createElement('span');name.textContent=preset.name;
  const meta=document.createElement('small');meta.textContent=`${config.port} · ${config.bytesize}${config.parity}${config.stopbits}${config.rtscts?' · RTS/CTS':''}`;
  main.append(name,meta);main.onclick=()=>{openConnection(config);setSidebar(false)};
  const go=document.createElement('button');go.type='button';go.className='preset-go';go.textContent='↗';go.title='直接连接';go.setAttribute('aria-label','直接用该预设连接');
  go.disabled=role!=='control';go.onclick=()=>action(()=>connectConfig(config));
  const del=document.createElement('button');del.type='button';del.className='preset-del';del.textContent='×';del.title='删除预设';del.setAttribute('aria-label','删除预设 '+preset.name);
  del.disabled=role!=='control';del.onclick=()=>action(async()=>{await api('/presets/'+encodeURIComponent(preset.id),undefined,'DELETE');await poll();notice('已删除预设 '+preset.name)});
  row.append(main,go,del);list.append(row);
 }
 $('preset-count').textContent=String(presets.length);
}
function selectSession(value){sid=value;cursor=0;records=[];selected=null;paused=false;pauseCount=0;selectionVersion++;$('search').value='';updatePause();evidence(null);updateContext();renderSessionList();render();controls()}
function timeOf(wall){const d=new Date(wall*1000);return d.toLocaleTimeString('zh-CN',{hour12:false})+'.'+String(d.getMilliseconds()).padStart(3,'0')}
function evidence(row){
 selected=row;$('evidence-empty').hidden=Boolean(row);$('evidence-content').hidden=!row;
 if(!row)return;
 const linked=row.records,first=linked[0],last=linked[linked.length-1],size=linked.reduce((n,r)=>n+transcript.bytesOf(r).length,0);
 $('evidence-id').textContent=first.id===last.id?'#'+first.id:`#${first.id}–#${last.id}`;
 $('evidence-direction').textContent=row.direction==='rx'?'RX · 串口接收':'TX · 驱动接受发送';
 $('evidence-time').textContent=new Date(first.wall*1000).toLocaleString('zh-CN')+' / '+timeOf(first.wall);
 $('evidence-size').textContent=size.toLocaleString()+' bytes';
 $('evidence-range').textContent=`会话 ${sid}\n${mode==='log'?'关联原始分块范围（可能包含邻近行）':'原始记录'}：#${first.id}–#${last.id}`;
 let remaining=4096,preview=[];for(const r of linked){const bytes=transcript.bytesOf(r).slice(0,remaining);preview.push(transcript.hexOf(bytes));remaining-=bytes.length;if(!remaining)break}
 $('evidence-hex').textContent=preview.join('\n');$('evidence-limit').hidden=size<=4096;
}
function choose(row,node){evidence(row);for(const el of $('terminal').querySelectorAll('.selected'))el.classList.remove('selected');node.classList.add('selected');showDetails(true)}
let streamKey='',streamKeys=[],streamNodes=[];
function rowKey(row){const first=row.records[0],last=row.records[row.records.length-1];
 return row.direction+'#'+first.id+'#'+last.id+'#'+row.text.length+'#'+(row.truncated?1:0)}
function rowNode(row){
 if(mode==='records'){const node=document.createElement('button');node.type='button';node.className='raw-row';const record=row.records[0];
  for(const text of ['#'+record.id,timeOf(record.wall),record.direction.toUpperCase(),String(transcript.bytesOf(record).length),row.text]){const cell=document.createElement('span');cell.textContent=text;node.append(cell)}
  node.lastChild.className='raw-preview';return node}
 const node=document.createElement('div');node.className='record logline '+row.direction;node.tabIndex=0;node.setAttribute('role','button');
 node.setAttribute('aria-label','查看关联原始记录 #'+row.records[0].id);
 const time=document.createElement('span');time.className='time';time.textContent=timeOf(row.wall);
 const direction=document.createElement('span');direction.className='direction';direction.textContent=row.direction.toUpperCase();
 const text=document.createElement('pre');text.textContent=(row.text||' ')+(row.truncated?' … [显示截断]':'');
 node.append(time,direction,text);
 node.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(row,node)}};
 return node;
}
function entryNode(entry){
 if(entry.kind==='header'){const header=document.createElement('div');header.className='raw-header';for(const label of ['记录','时间','方向','字节','HEX 预览']){const cell=document.createElement('span');cell.textContent=label;header.append(cell)}return header}
 if(entry.kind==='empty'){const empty=document.createElement('div');empty.className='empty';empty.textContent=entry.text;return empty}
 const node=rowNode(entry.row);node.onclick=()=>choose(entry.row,node);
 if(selected&&selected.records[0].id===entry.row.records[0].id)node.classList.add('selected');
 return node;
}
/* Only touch the DOM that actually changed; rebuilding every row each tick makes the log stutter. */
function syncStream(area,entries,key){
 if(key!==streamKey){streamKey=key;streamKeys=[];streamNodes=[];area.replaceChildren()}
 const drop=node=>{if(node.parentNode===area)area.removeChild(node)};
 const nextKeys=entries.map(e=>e.key);
 if(nextKeys.length){const present=new Set(nextKeys);let start=0;
  while(start<streamKeys.length&&!present.has(streamKeys[start]))start++;
  if(start){for(let i=start-1;i>=0;i--)drop(streamNodes[i]);streamNodes.splice(0,start);streamKeys.splice(0,start)}}
 let common=0;const limit=Math.min(streamKeys.length,nextKeys.length);
 while(common<limit&&streamKeys[common]===nextKeys[common])common++;
 for(let i=streamKeys.length-1;i>=common;i--)drop(streamNodes[i]);
 streamNodes.length=common;streamKeys.length=common;
 if(common<entries.length){const fragment=document.createDocumentFragment();
  for(let i=common;i<entries.length;i++){const node=entryNode(entries[i]);fragment.append(node);streamNodes.push(node);streamKeys.push(entries[i].key)}
  area.append(fragment)}
}
function render(){
 if(paused)return;
 const area=$('terminal'),previous=area.scrollTop,query=$('search').value.toLowerCase(),encoding=$('encoding').value;
 const rows=transcript.rowsOf(records,mode,encoding);
 const visible=query?rows.filter(r=>r.text.toLowerCase().includes(query)):rows;
 const entries=[];
 if(mode==='records'&&visible.length)entries.push({kind:'header',key:'header'});
 for(const row of visible)entries.push({key:rowKey(row),row});
 if(!visible.length)entries.push({kind:'empty',key:'empty',text:query?'当前显示窗口中没有匹配内容。':sid?'等待数据；可切换历史会话或开始发送。':'连接设备或打开历史会话，开始查看数据。'});
 // One record may produce several lines; keep their keys unique without breaking order.
 const seen=new Map();
 for(const entry of entries){const n=(seen.get(entry.key)||0)+1;seen.set(entry.key,n);if(n>1)entry.key+='@'+n}
 syncStream(area,entries,sid+'|'+mode+'|'+encoding+'|'+query);
 if($('autoscroll').checked)area.scrollTop=area.scrollHeight;else area.scrollTop=previous;
 $('record-count').textContent=`${visible.length} 条可见${mode==='log'?'日志':'记录'} · 已载入 ${records.length} 条原始记录`;
}
function updatePause(){ $('paused').hidden=!paused;$('pause').textContent=paused?'继续显示':'暂停显示';$('pause-label').textContent=`显示已暂停，新增 ${pauseCount} 条原始记录；采集继续。`}
function setPause(value){paused=value;if(!paused)pauseCount=0;updatePause();render()}
async function refreshPorts(){const ports=await api('/ports');$('port-list').replaceChildren();for(const p of [...ports,{port:'loop://',description:'软件回环测试'}]){const o=document.createElement('option');o.value=p.port;o.label=p.description;$('port-list').append(o)}}
function fillConfig(c){if(!c)return;$('port').value=c.port??'';$('baud').value=c.baudrate??115200;$('bits').value=c.bytesize??8;$('parity').value=c.parity??'N';$('stop').value=c.stopbits??1;$('flow').value=String(Boolean(c.rtscts))}
function populateConfig(){fillConfig(status?.config)}
function openConnection(config){fillConfig(config||status?.config);$('connection-error').hidden=true;$('connection-help').textContent=status?.state==='connected'?'当前连接参数仅供查看。修改参数需要先断开设备。':'参数会保存在会话中。部分设备可能因 DTR/RTS 状态变化复位。';$('connection-dialog').showModal();controls();refreshPorts().catch(e=>{$('connection-error').textContent=e.message;$('connection-error').hidden=false})}
async function poll(){
 if(!token)return;
 try{const fresh=await api('/status');status=fresh;reachable=true;role=fresh.role;$('auth').hidden=true;sessionStorage.setItem('devtoolnext-token',token);
 $('role').textContent=role==='control'?'控制权限':'只读访问';document.body.dataset.connection=fresh.state;
 $('state').textContent={connected:'● 已连接',disconnected:'○ 未连接',fault:'! 连接故障',connecting:'连接中'}[fresh.state]||fresh.state;
 $('rx').textContent='RX '+fresh.rx_bytes.toLocaleString()+' B';$('tx').textContent='TX '+fresh.tx_bytes.toLocaleString()+' B';
 $('record-state').textContent=fresh.error?'录制异常':fresh.state==='connected'?'原始录制中':fresh.session_id?'已保存':'待开始';
 $('service-state').textContent='设备服务 · 已连接';
 if(fresh.error)notice(fresh.error,true);else if(noticeSource==='service')notice('');
 presets=fresh.presets||[];const presetSig=JSON.stringify(presets.map(p=>[p.id,p.name,p.used]));if(presetSig!==presetSignature){presetSignature=presetSig;renderPresets()}
 const key=JSON.stringify(fresh.config);if(key!==activeConfigKey){activeConfigKey=key;if(!$('connection-dialog').open)populateConfig()}
 if(sessions.length===0||tickCount%3===1){const nextSessions=await api('/sessions');sessions=nextSessions;
  const signature=JSON.stringify(sessions.map(s=>[s.id,s.ended,s.error]));if(signature!==sessionSignature){sessionSignature=signature;if(!sid&&sessions.length)selectSession(fresh.session_id||sessions[0].id);else renderSessionList()}}
 updateContext();controls();
 if(sid){const requestedSid=sid,version=selectionVersion;let fetched=0;
  // Keep pulling while the server reports more, otherwise a fast device outruns the page size.
  for(let attempt=0;attempt<8;attempt++){const page=await api(`/sessions/${sid}/records?after=${cursor}&limit=800`);if(requestedSid!==sid||version!==selectionVersion)return;if(!page.records.length)break;cursor=page.next_cursor;records.push(...page.records);fetched+=page.records.length;if(!page.has_more)break}
  if(fetched){records=records.slice(-800);if(paused){pauseCount+=fetched;updatePause()}else render()}}
 }catch(e){reachable=false;controls();$('service-state').textContent='设备服务 · 访问失败';$('state').textContent='! 服务不可达';$('record-state').textContent='录制状态未知';notice(e.name==='TimeoutError'?'设备服务响应超时，状态未知。':e.message,true)}
}
async function action(fn){if(busy)return;busy=true;controls();if(noticeSource!=='service')notice('');try{await fn()}catch(e){notice(e.message);if($('connection-dialog').open){$('connection-error').textContent=e.message;$('connection-error').hidden=false}}finally{busy=false;controls()}}
$('style').onchange=()=>appearance($('style').value,styleDefaults[$('style').value]);$('theme').onclick=()=>appearance(settings.style,settings.theme==='dark'?'light':'dark');appearance(settings.style,settings.theme);
$('login').onclick=()=>action(async()=>{token=$('token').value.trim();await poll();if(reachable)await refreshPorts()});$('token').onkeydown=e=>{if(e.key==='Enter')$('login').click()};
async function connectConfig(config){
 if(!config.port)throw Error('请填写设备端口');
 const connected=await api('/connect',config);status=connected;selectSession(connected.session_id);
 sessionSignature='';presetSignature='';$('connection-dialog').close();setSidebar(false);await poll();
}
$('open-connect').onclick=()=>openConnection();$('config').onclick=()=>openConnection();$('cancel-connect').onclick=()=>$('connection-dialog').close();$('refresh').onclick=()=>action(refreshPorts);
$('connect-form').onsubmit=e=>{e.preventDefault();action(()=>connectConfig(formConfig()))};
$('save-preset').onclick=()=>action(async()=>{
 const config=$('connection-dialog').open?formConfig():(status?.config||($('port').value.trim()?formConfig():null));
 if(!config||!config.port)throw Error('请先在连接设置里填写端口参数');
 const saved=await api('/presets',{name:'',config});
 presetSignature='';await poll();notice('已保存预设：'+saved.name);
});
$('disconnect').onclick=()=>action(async()=>{await api('/disconnect',{});await poll()});
$('send-form').onsubmit=e=>{e.preventDefault();if($('send').disabled)return;action(async()=>{const hex=$('format').value==='hex';const ending=hex?'':({lf:'\n',crlf:'\r\n',cr:'\r',none:''}[$('ending').value]||'');const result=await api('/send',{data:$('payload').value,format:$('format').value,ending,encoding:$('encoding').value});$('input-hint').textContent=`驱动已接受 ${result.written} 字节；设备执行结果未知。`})};
function syncEnding(){const hex=$('format').value==='hex';$('ending').disabled=hex;$('ending').title=hex?'HEX 按字节原样发出，不附加换行':'';if(hex)$('ending').value='none';else if($('ending').value==='none')$('ending').value='lf'}
$('format').onchange=syncEnding;syncEnding();
$('payload').onkeydown=e=>{if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();$('send-form').requestSubmit()}};
$('pause').onclick=()=>setPause(!paused);$('resume').onclick=()=>setPause(false);$('clear').onclick=()=>{records=[];selected=null;selectionVersion++;evidence(null);setPause(false)};
let searchTimer=0;
$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(render,120)};
$('encoding').oninput=render;
for(const tab of document.querySelectorAll('[data-mode]'))tab.onclick=()=>{mode=tab.dataset.mode;for(const t of document.querySelectorAll('[data-mode]')){t.classList.toggle('active',t===tab);t.setAttribute('aria-selected',String(t===tab))}$('terminal').setAttribute('aria-labelledby',tab.id);selected=null;evidence(null);render()};
$('session-search').oninput=renderSessionList;$('active-device').onclick=()=>{if(status?.session_id){selectSession(status.session_id);setSidebar(false)}else if(role==='control')openConnection()};
$('details').onclick=()=>showDetails($('content').classList.contains('inspect-hidden')||(innerWidth<=960&&!$('content').classList.contains('show-overlay')));$('close-details').onclick=()=>showDetails(false);
$('devices-toggle').onclick=()=>setSidebar(!document.querySelector('.shell').classList.contains('devices-open'));$('sidebar-backdrop').onclick=()=>setSidebar(false);
$('export').onclick=()=>action(async()=>{if(!sid)throw Error('请先选择会话');const response=await fetch(`/api/sessions/${sid}/export`,{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(60000)});if(!response.ok)throw Error('导出失败');const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`session-${sid}.jsonl`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
window.addEventListener('resize',()=>{if(innerWidth>960)$('content').classList.remove('show-overlay');if(innerWidth>680)setSidebar(false);applyLayout()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){showDetails(false);setSidebar(false)}if(e.ctrlKey&&e.key.toLowerCase()==='f'&&!$('connection-dialog').open){e.preventDefault();$('search').focus()}});
$('service-address').textContent=location.host;controls();render();
async function tick(){tickCount++;await poll();setTimeout(tick,300)}tick();

/* Module resizing: the separators remember their size in localStorage. */
const layoutDefaults={sidebar:224,inspector:280,composer:0},layout={...layoutDefaults};
try{const saved=JSON.parse(localStorage.getItem('devtoolnext-layout')||'{}');for(const k of Object.keys(layout))if(Number.isFinite(saved[k])&&saved[k]>0)layout[k]=saved[k]}catch{}
function saveLayout(){try{localStorage.setItem('devtoolnext-layout',JSON.stringify(layout))}catch{}}
function applyLayout(){
 const shell=document.querySelector('.shell'),content=$('content'),form=$('send-form');
 const wide=innerWidth>680,desk=innerWidth>960,hidden=content.classList.contains('inspect-hidden');
 shell.style.gridTemplateColumns=wide?`${Math.round(layout.sidebar)}px minmax(0,1fr)`:'';
 content.style.gridTemplateColumns=desk&&!hidden?`minmax(0,1fr) ${Math.round(layout.inspector)}px`:'';
 form.style.height=layout.composer?`${Math.round(layout.composer)}px`:'';
}
function bindDrag(id,{axis,read,write,min,max}){
 const handle=$(id);if(!handle)return;
 const clamp=v=>Math.round(Math.max(min(),Math.min(max(),v)));
 handle.addEventListener('pointerdown',e=>{
  if(e.button!==0)return;
  e.preventDefault();handle.setPointerCapture(e.pointerId);handle.classList.add('active');document.body.classList.add('resizing');
  const start=axis==='x'?e.clientX:e.clientY,base=read();
  const move=ev=>{const delta=axis==='x'?ev.clientX-start:start-ev.clientY;write(clamp(base+delta));applyLayout()};
  const done=()=>{handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',done);handle.removeEventListener('pointercancel',done);handle.classList.remove('active');document.body.classList.remove('resizing');saveLayout()};
  handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',done);handle.addEventListener('pointercancel',done);
 });
 handle.addEventListener('dblclick',()=>{write(layoutDefaults[id.replace('drag-','')]);applyLayout();saveLayout()});
 handle.addEventListener('keydown',e=>{
  const step=e.shiftKey?48:16;let delta=0;
  if(axis==='x'&&e.key==='ArrowLeft')delta=-step;else if(axis==='x'&&e.key==='ArrowRight')delta=step;
  else if(axis==='y'&&e.key==='ArrowUp')delta=step;else if(axis==='y'&&e.key==='ArrowDown')delta=-step;else return;
  e.preventDefault();write(clamp(read()+delta));applyLayout();saveLayout();
 });
}
bindDrag('drag-sidebar',{axis:'x',read:()=>layout.sidebar,write:v=>{layout.sidebar=v},min:()=>160,max:()=>Math.min(460,innerWidth*0.5)});
bindDrag('drag-inspector',{axis:'x',read:()=>layout.inspector,write:v=>{layout.inspector=v},min:()=>220,max:()=>Math.max(260,Math.min(620,innerWidth-520))});
bindDrag('drag-composer',{axis:'y',read:()=>layout.composer||$('send-form').offsetHeight,write:v=>{layout.composer=v},min:()=>88,max:()=>Math.max(140,Math.round($('content').clientHeight-200))});
applyLayout();
