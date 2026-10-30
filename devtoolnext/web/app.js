const $=id=>document.getElementById(id);
const transcript=globalThis.DevToolTranscript;
let token=new URLSearchParams(location.hash.slice(1)).get('token')||sessionStorage.getItem('devtoolnext-token')||'';
history.replaceState(null,'',location.pathname);
let loopTimer=0,loopRemaining=0,loopBusy=false;
let sid='',cursor=0,records=[],status=null,role='',reachable=false,busy=false,paused=false,pauseCount=0,selectionVersion=0;
let activeConfigKey='',noticeSource='',presets=[],presetSignature='',connections=[],activeId='',settings={style:'studio',theme:'dark'};
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
function liveSelected(){return Boolean(status?.state==='connected'&&status.session_id===sid)}
function controls(){
 const canControl=role==='control'&&reachable&&!busy;
 $('connect').disabled=!canControl;$('open-connect').disabled=!canControl;
 $('config').disabled=!reachable||!status?.config;
 $('disconnect').disabled=!canControl||!['connected','fault'].includes(status?.state);
 $('send').disabled=!canControl||!liveSelected();$('payload').disabled=!canControl||!liveSelected();
 $('export').disabled=!sid||!reachable||busy;
 if(!loopTimer)$('input-hint').textContent=!reachable?'请先认证并连接设备服务。':role==='view'?'只读访问：可查看和导出，不能发送或断开设备。':!liveSelected()?(sid?'当前会话未在采集，不能向历史记录发送。':'连接设备后可发送。'):'发送成功表示驱动接受字节，不代表设备命令完成。';
 $('refresh').disabled=!reachable||busy;$('login').disabled=busy;
 const live=canControl&&status?.state==='connected';
 const serial=live&&(status?.config?.type||'serial')==='serial';
 $('dtr').disabled=!serial;$('rts').disabled=!serial;$('dtr').hidden=!serial;$('rts').hidden=!serial;
 for(const [id,value] of [['dtr',status?.dtr],['rts',status?.rts]]){const on=Boolean(value);$(id).classList.toggle('on',on);$(id).setAttribute('aria-pressed',String(on))}
 $('loop-toggle').disabled=!canControl||!liveSelected();
}
async function api(path,body,method,timeout=8000){
 const response=await fetch('/api'+path,{method:method||(body===undefined?'GET':'POST'),headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(timeout)});
 if(!response.ok){const error=await response.json().catch(()=>({}));if(response.status===401){role='';reachable=false;$('auth').hidden=false;controls()}throw Error(typeof error.detail==='string'?error.detail:'请求失败：'+response.status)}
 return response.json();
}
function setSidebar(open){document.querySelector('.shell').classList.toggle('devices-open',open);$('sidebar-backdrop').hidden=!open;$('devices-toggle').setAttribute('aria-expanded',String(open))}
function connTarget(config){
 if(!config)return '';
 const kind=config.type||'serial';
 if(kind==='serial')return config.port||'';
 const port=config.tcp_port||(kind==='ssh'?22:23);
 return `${config.port}:${port}`;
}
function updateContext(){
 const config=status?.config,kind=config?.type||'serial';
 const target=connTarget(config);
 $('device-title').textContent=!config?'设备工作台':kind==='serial'?`串口 · ${config.port}`:kind==='ssh'?`SSH · ${target}`:`Telnet · ${target}`;
 $('context-status').textContent=liveSelected()?'已连接 · 原始录制中 · '+(role==='control'?'控制权限':'只读观察'):status?.state==='fault'?'连接故障，采集已停止':sid?'会话已结束，可查看本次记录或重新连接':'选择连接类型，开始采集。';
 $('send-target').textContent=liveSelected()?target:'未连接';
 $('device-name').textContent=config?(config.port||'暂无连接'):'暂无连接';
 $('device-config').textContent=config?(kind==='serial'?`${config.baudrate} · ${config.bytesize}${config.parity}${config.stopbits} · ${status.state==='connected'?'已连接':'已断开'}`:`${kind.toUpperCase()} · ${target} · ${status.state==='connected'?'已连接':'已断开'}`):'连接设备开始采集';
 $('active-device').classList.toggle('selected',Boolean(sid&&sid===status?.session_id));
 $('config').textContent=config?(kind==='serial'?`${config.baudrate} · ${config.bytesize}${config.parity}${config.stopbits}`:target):'连接参数';
}
function updateLogPath(fresh){
 const file=fresh?.log_file||'';
 $('log-file').hidden=!file;
 $('log-file').textContent=file?file.split(/[\\/]/).pop():'';
 $('log-file').title=file;
}
function formConfig(){
 const type=$('conn-type').value;
 const config={type,port:$('port').value.trim(),baudrate:Number($('baud').value)||115200,
  bytesize:Number($('bits').value)||8,parity:$('parity').value,stopbits:Number($('stop').value)||1,
  rtscts:$('flow').value==='true'};
 if(type==='serial')return config;
 config.tcp_port=Number($('tcp-port').value)||0;
 config.username=$('ssh-user').value.trim();
 config.password=$('ssh-pass').value;
 config.keyfile=$('ssh-key').value.trim();
 return config;
}
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
function focusSession(value){
 if(value===sid)return;
 sid=value||'';cursor=0;records=[];termProjector=null;termKey='';paused=false;pauseCount=0;selectionVersion++;
 updatePause();
 if(sid)startLive();
}
function activate(cid){
 if(cid===activeId)return;
 activeId=cid;
 const found=connections.find(c=>c.id===cid)||null;
 status=found;updateLogPath(found);
 // Network lines treat CR and LF as separate Enter keys, so CRLF would submit twice.
 $('ending').value='cr';
 termKey='';
 focusSession(found?.session_id||'');
 updateContext();renderTabs();render();controls();
}
function renderTabs(){
 const bar=$('conn-tabs');bar.replaceChildren();
 for(const conn of connections){
  const tab=document.createElement('div');
  tab.className='conn-tab'+(conn.id===activeId?' active':'');
  tab.setAttribute('role','tab');tab.tabIndex=0;tab.setAttribute('aria-selected',String(conn.id===activeId));
  const dot=document.createElement('span');dot.className='conn-dot '+(conn.state==='connected'?'on':conn.state==='fault'?'bad':'off');
  const name=document.createElement('span');name.className='conn-name';name.textContent=conn.label||conn.config?.port||'连接';
  name.title=conn.error||name.textContent;
  const close=document.createElement('button');close.type='button';close.className='conn-close';close.textContent='×';
  close.title='关闭该连接';close.setAttribute('aria-label','关闭 '+name.textContent);close.disabled=role!=='control';
  close.onclick=e=>{e.stopPropagation();action(()=>closeConnection(conn.id))};
  tab.append(dot,name,close);
  tab.onclick=()=>activate(conn.id);
  tab.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();activate(conn.id)}};
  bar.append(tab);
 }
 if(connections.length){
  const add=document.createElement('button');add.type='button';add.className='conn-add';add.textContent='＋';
  add.title='新建连接';add.setAttribute('aria-label','新建连接');add.disabled=role!=='control';
  add.onclick=()=>openConnection();bar.append(add);
 }
}
let streamKey='',streamKeys=[],streamNodes=[];
let termProjector=null,termKey='';
function termId(){return sid+'|'+$('encoding').value}
function currentTerminal(){
 const key=termId();
 if(termKey!==key){termProjector=transcript.createTerminal($('encoding').value,false);termKey=key;termProjector.push(records)}
 return termProjector;
}
function termLineNode(line,cursor){
 const node=document.createElement('div');node.className='term-line';
 const text=line.text,styles=line.styles;
 let i=0;
 while(i<text.length){
  const cls=styles[i]||'';let j=i+1;
  while(j<text.length&&(styles[j]||'')===cls)j++;
  if(cursor>=i&&cursor<j){
   const head=document.createElement('span');if(cls)head.className=cls;head.textContent=text.slice(i,cursor);node.append(head);
   const caret=document.createElement('span');caret.className='term-cursor';caret.textContent='\u00a0';node.append(caret);
   const tail=document.createElement('span');if(cls)tail.className=cls;tail.textContent=text.slice(cursor,j);node.append(tail);
  }else{
   const span=document.createElement('span');if(cls)span.className=cls;span.textContent=text.slice(i,j);node.append(span);
  }
  i=j;
 }
 if(cursor>=text.length){const caret=document.createElement('span');caret.className='term-cursor';caret.textContent='\u00a0';node.append(caret)}
 if(!text&&cursor<0)node.append(document.createTextNode('\u00a0'));
 return node;
}
function renderTerm(area,previous,encoding){
 const term=currentTerminal();
 const entries=term.lines.map(line=>{
  const caret=line===term.cursorLine?term.col:-1;
  return {kind:'term',key:'t'+line.id+':'+line.rev+':'+caret,line,cursor:caret};
 });
 syncStream(area,entries,'term|'+sid+'|'+encoding);
 if($('autoscroll').checked)area.scrollTop=area.scrollHeight;else area.scrollTop=previous;
 $('record-count').textContent=`终端 ${term.lines.length} 行 · 已载入 ${records.length} 条原始记录`;
}
const keyQueue=[];
let keyTimer=0;
function queueInput(bytes){
 for(const byte of bytes)keyQueue.push(byte);
 if(!keyTimer)keyTimer=setTimeout(flushInput,12);
}
async function flushInput(){
 keyTimer=0;
 if(!keyQueue.length)return;
 const bytes=keyQueue.splice(0,keyQueue.length);
 if(!status||!liveSelected()){notice('连接已断开，按键未发送');return}
 const hex=bytes.map(b=>b.toString(16).padStart(2,'0')).join('');
 try{await api(`/connections/${status.id}/send`,{data:hex,format:'hex',ending:''})}
 catch(e){notice('发送失败：'+e.message)}
}
function keyBytes(e){
 const k=e.key;
 if(e.ctrlKey&&k.length===1&&k.toLowerCase()!=='v'){
  const code=k.toUpperCase().charCodeAt(0)-64;
  if(code>=0&&code<32)return [code];
 }
 if(e.metaKey&&k!=='Meta')return null;
 if(k==='Enter')return [13];
 if(k==='Backspace')return [127];
 if(k==='Tab')return [9];
 if(k==='Escape')return [27];
 if(k==='ArrowUp')return [27,91,65];
 if(k==='ArrowDown')return [27,91,66];
 if(k==='ArrowRight')return [27,91,67];
 if(k==='ArrowLeft')return [27,91,68];
 if(k==='Home')return [27,91,72];
 if(k==='End')return [27,91,70];
 if(k==='Delete')return [27,91,51,126];
 if(k.length===1)return [...new TextEncoder().encode(k)];
 return null;
}
$('terminal').addEventListener('keydown',e=>{
 if(!liveSelected())return;
 if(e.ctrlKey&&e.key.toLowerCase()==='c'&&String(getSelection()).length)return;
 const bytes=keyBytes(e);
 if(!bytes)return;
 e.preventDefault();
 queueInput(bytes);
});
$('terminal').addEventListener('paste',e=>{
 if(!liveSelected())return;
 e.preventDefault();
 const text=(e.clipboardData||window.clipboardData).getData('text');
 if(text)queueInput(new TextEncoder().encode(text.replace(/\r?\n/g,'\r')));
});
$('terminal').addEventListener('mousedown',()=>$('terminal').focus());
function entryNode(entry){
 return termLineNode(entry.line,entry.cursor);
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
 const area=$('terminal'),previous=area.scrollTop;
 renderTerm(area,previous,$('encoding').value);
}
function updatePause(){ $('paused').hidden=!paused;$('pause').textContent=paused?'继续显示':'暂停显示';$('pause-label').textContent=`显示已暂停，新增 ${pauseCount} 条原始记录；采集继续。`}
function setPause(value){paused=value;if(!paused)pauseCount=0;updatePause();render()}
async function refreshPorts(){const ports=await api('/ports');$('port-list').replaceChildren();for(const p of [...ports,{port:'loop://',description:'软件回环测试'}]){const o=document.createElement('option');o.value=p.port;o.label=p.description;$('port-list').append(o)}}
function syncConnType(){
 const type=$('conn-type').value,serial=type==='serial';
 $('serial-fields').hidden=!serial;$('serial-hint').hidden=!serial;
 $('tcp-fields').hidden=serial;$('ssh-fields').hidden=type!=='ssh';
 $('refresh').hidden=!serial;$('refresh').disabled=!serial||!reachable||busy;
 $('port').setAttribute('list',serial?'port-list':'');
 $('port').placeholder=serial?'COM3 或 loop:// 软件回环':'192.168.1.10';
 $('tcp-port').placeholder=type==='ssh'?'22':'23';
 $('port-label').firstChild.textContent=serial?'设备端口 ':'主机地址 ';
}
function fillConfig(c){
 if(!c)return;
 $('conn-type').value=c.type??'serial';syncConnType();
 $('port').value=c.port??'';
 $('baud').value=c.baudrate??115200;$('bits').value=c.bytesize??8;$('parity').value=c.parity??'N';$('stop').value=c.stopbits??1;$('flow').value=String(Boolean(c.rtscts));
 $('tcp-port').value=c.tcp_port??'';
 $('ssh-user').value=c.username??'';$('ssh-pass').value='';$('ssh-key').value=c.keyfile??'';
}
function populateConfig(){fillConfig(status?.config)}
function openConnection(config){fillConfig(config||status?.config);$('connection-error').hidden=true;$('connection-dialog').showModal();controls();if($('conn-type').value==='serial')refreshPorts().catch(e=>{$('connection-error').textContent=e.message;$('connection-error').hidden=false})}
async function poll(){
 if(!token)return;
 try{
  const data=await api('/connections');
  connections=data.connections||[];role=data.role;reachable=true;
  $('auth').hidden=true;sessionStorage.setItem('devtoolnext-token',token);
  $('role').textContent=role==='control'?'控制权限':'只读访问';
  presets=data.presets||[];
  const presetSig=JSON.stringify(presets.map(p=>[p.id,p.name,p.used]));
  if(presetSig!==presetSignature){presetSignature=presetSig;renderPresets()}
  if(!connections.length)activeId='';
  else if(!connections.some(c=>c.id===activeId))activeId=connections[0].id;
  status=connections.find(c=>c.id===activeId)||null;
  updateLogPath(status);
  if(!status)focusSession('');
  else if(status.state==='connected'&&status.session_id)focusSession(status.session_id);
  document.body.dataset.connection=status?.state||'disconnected';
  $('state').textContent={connected:'● 已连接',disconnected:'○ 未连接',fault:'! 连接故障',connecting:'连接中'}[status?.state]||'○ 未连接';
  $('rx').textContent='RX '+(status?.rx_bytes||0).toLocaleString()+' B';
  $('tx').textContent='TX '+(status?.tx_bytes||0).toLocaleString()+' B';
  $('record-state').textContent=!status?'待开始':status.error?'录制异常':status.state==='connected'?'原始录制中':status.session_id?'已保存':'待开始';
  $('service-state').textContent=role?'设备服务 · 已连接':'设备服务 · 等待认证';
  if(status?.error)notice(status.error,true);else if(noticeSource==='service')notice('');
  updateContext();renderTabs();controls();
 }catch(e){reachable=false;controls();$('service-state').textContent='设备服务 · 访问失败';$('state').textContent='! 服务不可达';$('record-state').textContent='录制状态未知';notice(e.name==='TimeoutError'?'设备服务响应超时，状态未知。':e.message,true)}
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let liveToken=0,renderQueued=false;
function scheduleRender(){
 if(renderQueued||paused)return;
 renderQueued=true;
 requestAnimationFrame(()=>{renderQueued=false;render()});
}
function appendRecords(list){
 if(!list.length)return;
 records.push(...list);
 if(records.length>800)records=records.slice(-800);
 if(termProjector&&termKey===termId())termProjector.push(list);
 if(paused){pauseCount+=list.length;updatePause()}else scheduleRender();
}
/* Long poll: the server answers the moment a batch lands, so output streams instead of ticking. */
async function startLive(){
 const my=++liveToken;
 while(my===liveToken){
  if(!sid||!token){await sleep(300);continue}
  const requestedSid=sid,version=selectionVersion;
  let page;
  try{page=await api(`/sessions/${sid}/records/live?after=${cursor}&timeout=20`,undefined,undefined,25000)}
  catch(e){
   if(my!==liveToken)return;
   reachable=false;controls();$('service-state').textContent='设备服务 · 访问失败';
   notice(e.name==='TimeoutError'?'设备服务响应超时，状态未知。':e.message,true);
   await sleep(1200);continue;
  }
  if(my!==liveToken||requestedSid!==sid||version!==selectionVersion)return;
  reachable=true;
  if(typeof page.rx_bytes==='number'){$('rx').textContent='RX '+page.rx_bytes.toLocaleString()+' B';$('tx').textContent='TX '+page.tx_bytes.toLocaleString()+' B'}
  if(page.records.length){cursor=page.next_cursor;appendRecords(page.records)}
  if(!page.records.length&&page.state!=='connected'&&page.state!=='connecting')return;
 }
}
async function action(fn){if(busy)return;busy=true;controls();if(noticeSource!=='service')notice('');try{await fn()}catch(e){notice(e.message);if($('connection-dialog').open){$('connection-error').textContent=e.message;$('connection-error').hidden=false}else if($('settings-dialog').open){$('settings-error').textContent=e.message;$('settings-error').hidden=false}else if($('mcp-dialog').open){$('mcp-error').textContent=e.message;$('mcp-error').hidden=false}}finally{busy=false;controls()}}
$('style').onchange=()=>appearance($('style').value,styleDefaults[$('style').value]);$('theme').onclick=()=>appearance(settings.style,settings.theme==='dark'?'light':'dark');appearance(settings.style,settings.theme);
$('login').onclick=()=>action(async()=>{token=$('token').value.trim();await poll();if(reachable){await refreshPorts();await loadSettings()}});$('token').onkeydown=e=>{if(e.key==='Enter')$('login').click()};
async function connectConfig(config){
 if(!config.port)throw Error('请填写设备端口或主机地址');
 const created=await api('/connections',config);
 presetSignature='';$('connection-dialog').close();setSidebar(false);
 await poll();
 activate(created.id);
}
async function closeConnection(cid){
 stopLoop();
 await api('/connections/'+encodeURIComponent(cid),undefined,'DELETE');
 if(cid===activeId)activeId='';
 await poll();
}
$('open-connect').onclick=()=>openConnection();$('config').onclick=()=>openConnection();$('cancel-connect').onclick=()=>$('connection-dialog').close();$('refresh').onclick=()=>action(refreshPorts);
$('connect-form').onsubmit=e=>{e.preventDefault();action(()=>connectConfig(formConfig()))};
$('conn-type').onchange=syncConnType;syncConnType();
$('save-preset').onclick=()=>action(async()=>{
 const config=$('connection-dialog').open?formConfig():(status?.config||($('port').value.trim()?formConfig():null));
 if(!config||!config.port)throw Error('请先在连接设置里填写端口参数');
 const saved=await api('/presets',{name:'',config});
 presetSignature='';await poll();notice('已保存预设：'+saved.name);
});

let outputDir='',mcpEnabled=true;
function mcpConfigText(){
 const url=`http://${location.host}/mcp`;
 return JSON.stringify({mcpServers:{devtoolnext:{type:'http',url,headers:{Authorization:'Bearer '+token}}}},null,2);
}
function renderMcp(){
 $('mcp-enabled').checked=mcpEnabled;
 $('mcp-config').textContent=mcpConfigText();
 $('mcp-note').textContent=role==='control'
  ?'当前是控制密钥：Agent 可以新建连接、发送命令、读取回复。'
  :'当前是只读密钥：Agent 只能读取，发送与建连会被拒绝。';
}
async function copyText(text){
 try{await navigator.clipboard.writeText(text);return true}catch{}
 const area=document.createElement('textarea');
 area.value=text;area.style.position='fixed';area.style.top='-1000px';
 document.body.append(area);area.select();
 try{document.execCommand('copy');return true}catch{return false}finally{area.remove()}
}
$('open-mcp').onclick=()=>action(async()=>{
 $('mcp-error').hidden=true;
 await loadSettings();
 renderMcp();
 $('mcp-dialog').showModal();
});
$('cancel-mcp').onclick=()=>$('mcp-dialog').close();
$('copy-mcp').onclick=()=>action(async()=>{
 const ok=await copyText(mcpConfigText());
 notice(ok?'MCP 配置已复制，可直接粘贴到客户端设置里':'复制失败，请手动选中配置文本');
});
$('mcp-form').onsubmit=e=>{e.preventDefault();action(async()=>{
 const saved=await api('/settings',{mcp_enabled:$('mcp-enabled').checked});
 mcpEnabled=saved.mcp_enabled!==false;
 $('mcp-dialog').close();
 notice(mcpEnabled?'MCP 接口已开启':'MCP 接口已关闭');
})};
async function loadSettings(){
 try{
  const data=await api('/settings');
  outputDir=data.output_dir||'';
  $('output-dir').textContent=outputDir;$('output-dir').title=outputDir;
  $('keep-raw').checked=data.keep_raw!==false;
  mcpEnabled=data.mcp_enabled!==false;
 }catch{}
}
$('open-settings').onclick=()=>action(async()=>{
 $('settings-error').hidden=true;await loadSettings();$('output-dir-input').value=outputDir;$('settings-dialog').showModal();
});
$('cancel-settings').onclick=()=>$('settings-dialog').close();
$('open-folder').onclick=()=>action(()=>api('/reveal',{}));
$('settings-form').onsubmit=e=>{e.preventDefault();action(async()=>{
 const saved=await api('/settings',{output_dir:$('output-dir-input').value.trim(),keep_raw:$('keep-raw').checked});
 if(saved.output_dir){outputDir=saved.output_dir;$('output-dir').textContent=outputDir;$('output-dir').title=outputDir}
 $('settings-dialog').close();notice('新会话将写入：'+outputDir);
})};
$('disconnect').onclick=()=>action(async()=>{if(!status)throw Error('没有活动连接');await closeConnection(status.id)});

let sendHistory=[],historyIndex=-1;
try{const saved=JSON.parse(localStorage.getItem('devtoolnext-history')||'[]');if(Array.isArray(saved))sendHistory=saved.slice(-50)}catch{}
function rememberInput(text){
 if(!text)return;
 if(sendHistory[sendHistory.length-1]!==text)sendHistory.push(text);
 if(sendHistory.length>50)sendHistory=sendHistory.slice(-50);
 historyIndex=-1;
 try{localStorage.setItem('devtoolnext-history',JSON.stringify(sendHistory))}catch{}
}
async function sendPayload(silent){
 const data=$('payload').value;
 if(!data)throw Error('发送内容不能为空');
 const hex=$('format').value==='hex';
 const ending=hex?'':({lf:'\n',crlf:'\r\n',cr:'\r',none:''}[$('ending').value]||'');
 const result=await api(`/connections/${status.id}/send`,{data,format:$('format').value,ending,encoding:$('encoding').value});
 rememberInput(data);
 if(!silent)$('input-hint').textContent=`驱动已接受 ${result.written} 字节；设备执行结果未知。`;
 return result;
}
$('send-form').onsubmit=e=>{e.preventDefault();if($('send').disabled)return;action(()=>sendPayload(false))};
function syncEnding(){const hex=$('format').value==='hex';$('ending').disabled=hex;$('ending').title=hex?'HEX 按字节原样发出，不附加换行':'';if(hex)$('ending').value='none';else if($('ending').value==='none')$('ending').value='lf'}
$('format').onchange=syncEnding;syncEnding();

function stopLoop(message){
 if(!loopTimer)return;
 clearInterval(loopTimer);loopTimer=0;loopRemaining=0;
 $('loop-toggle').textContent='循环发送';$('loop-toggle').classList.remove('on');
 controls();
 if(message)notice(message);
}
async function loopTick(){
 if(loopBusy)return;
 if(!liveSelected()){stopLoop('设备已断开，循环发送已停止');return}
 loopBusy=true;
 try{await sendPayload(true)}
 catch(e){stopLoop('循环发送已停止：'+e.message);return}
 finally{loopBusy=false}
 if(loopRemaining>0){
  loopRemaining--;
  if(loopRemaining===0){stopLoop('循环发送完成');return}
 }
 $('input-hint').textContent=loopRemaining>0?`循环发送中，剩余 ${loopRemaining} 次`:'循环发送中（不限次数）';
}
$('loop-toggle').onclick=()=>{
 if(loopTimer){stopLoop();return}
 if($('send').disabled){notice('设备未连接，无法循环发送');return}
 if(!$('payload').value){notice('请先填写要循环发送的内容');return}
 const interval=Math.min(600000,Math.max(50,Number($('loop-interval').value)||1000));
 const count=Math.max(0,Number($('loop-count').value)||0);
 $('loop-interval').value=String(interval);$('loop-count').value=String(count);
 loopRemaining=count;
 $('loop-toggle').textContent='停止循环';$('loop-toggle').classList.add('on');
 loopTimer=setInterval(loopTick,interval);
 loopTick();
};
$('payload').onkeydown=e=>{
 const area=e.currentTarget;
 if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();$('send-form').requestSubmit();return}
 if(!sendHistory.length)return;
 if(e.key==='ArrowUp'&&!e.shiftKey&&area.selectionStart===0&&area.selectionEnd===0){
  e.preventDefault();
  historyIndex=historyIndex<0?sendHistory.length-1:Math.max(0,historyIndex-1);
  area.value=sendHistory[historyIndex];area.setSelectionRange(0,0);
  return;
 }
 if(e.key==='ArrowDown'&&!e.shiftKey&&historyIndex>=0&&area.selectionStart===area.value.length){
  e.preventDefault();
  historyIndex++;
  if(historyIndex>=sendHistory.length){historyIndex=-1;area.value=''}
  else{area.value=sendHistory[historyIndex];area.setSelectionRange(area.value.length,area.value.length)}
 }
};

const display={font:13};
try{Object.assign(display,JSON.parse(localStorage.getItem('devtoolnext-display')||'{}'))}catch{}
function applyDisplay(){
 $('terminal').style.fontSize=display.font+'px';
 $('fontsize').value=String(display.font);
 try{localStorage.setItem('devtoolnext-display',JSON.stringify(display))}catch{}
}
$('fontsize').onchange=()=>{display.font=Number($('fontsize').value)||13;applyDisplay()};
applyDisplay();
$('dtr').onclick=()=>action(async()=>{if(!status)throw Error('没有活动连接');await api(`/connections/${status.id}/lines`,{dtr:!Boolean(status.dtr)});await poll()});
$('rts').onclick=()=>action(async()=>{if(!status)throw Error('没有活动连接');await api(`/connections/${status.id}/lines`,{rts:!Boolean(status.rts)});await poll()});
$('pause').onclick=()=>setPause(!paused);$('resume').onclick=()=>setPause(false);$('clear').onclick=()=>{records=[];termProjector=null;termKey='';selectionVersion++;setPause(false)};
$('encoding').oninput=render;
$('active-device').onclick=()=>{if(status){activate(status.id);setSidebar(false)}else if(role==='control')openConnection()};
$('devices-toggle').onclick=()=>setSidebar(!document.querySelector('.shell').classList.contains('devices-open'));$('sidebar-backdrop').onclick=()=>setSidebar(false);
$('export').onclick=()=>action(async()=>{if(!sid)throw Error('还没有可导出的会话');const response=await fetch(`/api/sessions/${sid}/export`,{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(60000)});if(!response.ok)throw Error('导出失败');const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`session-${sid}.jsonl`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
window.addEventListener('resize',()=>{if(innerWidth>680)setSidebar(false);applyLayout()});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)render()});
document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key.toLowerCase()==='l'&&!$('connection-dialog').open&&!$('settings-dialog').open){e.preventDefault();$('clear').click();return}if(e.key==='Escape')setSidebar(false)});
$('service-address').textContent=location.host;controls();render();if(token)loadSettings();
async function tick(){await poll();setTimeout(tick,1000)}tick();

/* Module resizing: the separators remember their size in localStorage. */
const layoutDefaults={sidebar:224,composer:0},layout={...layoutDefaults};
try{const saved=JSON.parse(localStorage.getItem('devtoolnext-layout')||'{}');for(const k of Object.keys(layout))if(Number.isFinite(saved[k])&&saved[k]>0)layout[k]=saved[k]}catch{}
function saveLayout(){try{localStorage.setItem('devtoolnext-layout',JSON.stringify(layout))}catch{}}
function applyLayout(){
 const shell=document.querySelector('.shell'),form=$('send-form');
 const wide=innerWidth>680;
 shell.style.gridTemplateColumns=wide?`${Math.round(layout.sidebar)}px minmax(0,1fr)`:'';
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
bindDrag('drag-composer',{axis:'y',read:()=>layout.composer||$('send-form').offsetHeight,write:v=>{layout.composer=v},min:()=>88,max:()=>Math.max(140,Math.round($('content').clientHeight-200))});
applyLayout();
