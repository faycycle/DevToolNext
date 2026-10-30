const assert = require('node:assert/strict');
const { chromium } = require(process.argv[3] || 'playwright-core');
(async () => {
 const browser = await chromium.launch({ channel: 'msedge', headless: true });
 try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.argv[2]);
  await page.waitForFunction(()=>document.getElementById('role').textContent.includes('控制权限'));
  if(await page.locator('#disconnect').isEnabled()){await page.locator('#disconnect').click();await page.waitForFunction(()=>document.getElementById('state').textContent.includes('未连接'))}
  await page.locator('#open-connect').click();await page.locator('#port').fill('loop://');await page.locator('#connect').click();
  await page.waitForFunction(()=>!document.getElementById('send').disabled);
  await page.waitForFunction(()=>document.querySelectorAll('.preset-item').length>0);
  const sidebarWidth=await page.locator('.sidebar').evaluate(n=>n.getBoundingClientRect().width);
  const dragBox=await page.locator('#drag-sidebar').boundingBox();
  await page.mouse.move(dragBox.x+3,dragBox.y+150);await page.mouse.down();
  await page.mouse.move(dragBox.x+83,dragBox.y+150,{steps:5});await page.mouse.up();
  assert.ok((await page.locator('.sidebar').evaluate(n=>n.getBoundingClientRect().width))>sidebarWidth+50,'sidebar should be resizable');
  await page.locator('#payload').fill('设备正常 · loopback OK');await page.locator('#send').click();
  await page.waitForFunction(()=>[...document.querySelectorAll('.record.rx pre')].some(n=>n.textContent.includes('设备正常 · loopback OK')));
  assert.equal(await page.locator('.record.rx').count(),1,'RX fragments should merge into one line');
  const observer=await browser.newPage();await observer.goto(process.argv[2]);
  await observer.waitForFunction(()=>[...document.querySelectorAll('.record.rx pre')].some(n=>n.textContent.includes('loopback OK')));
  assert.equal(await page.locator('#session-label').textContent(),await observer.locator('#session-label').textContent());
  await page.locator('.record.rx').first().click();await page.waitForFunction(()=>!document.getElementById('evidence-content').hidden);
  assert.ok((await page.locator('#evidence-hex').textContent()).replace(/\s/g,'').includes('E8AEBE'));
  await page.locator('#close-details').click();
  await page.locator('#pause').click();await page.locator('#payload').fill('recording continues while display paused');await page.locator('#send').click();
  await observer.waitForFunction(()=>[...document.querySelectorAll('.record.rx pre')].some(n=>n.textContent.includes('display paused')));
  assert.ok(!(await page.locator('#terminal').textContent()).includes('display paused'));
  await page.locator('#resume').click();await page.waitForFunction(()=>document.getElementById('terminal').textContent.includes('display paused'));
  await page.locator('#tab-records').click();assert.ok(await page.locator('.raw-row').count()>=2);await page.locator('.raw-row').first().click();
  await page.locator('#close-details').click();await page.locator('#tab-log').click();
  for(const style of ['studio','graphite','lab','paper','midnight','terminal']){
   await page.locator('#style').selectOption(style);
   assert.equal(await page.locator('html').getAttribute('data-style'),style);
   await page.locator('#theme').click();
   await page.setViewportSize({width:390,height:844});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${style}: mobile overflow`);
   await page.setViewportSize({width:1440,height:1000});
  }
  await page.locator('#style').selectOption('studio');await page.locator('.record.rx').first().click();
  await page.screenshot({path:'build/workbench.png',fullPage:true});
  await page.locator('#style').selectOption('paper');await page.reload();
  await page.waitForFunction(()=>document.getElementById('role').textContent.includes('控制权限'));
  assert.equal(await page.locator('html').getAttribute('data-style'),'paper');
  const download=page.waitForEvent('download');await page.locator('#export').click();assert.ok((await download).suggestedFilename().endsWith('.jsonl'));
  if(process.argv[4]){
   const viewer=await browser.newPage();await viewer.goto(process.argv[4]);
   await viewer.waitForFunction(()=>document.getElementById('role').textContent.includes('只读'));
   assert.equal(await viewer.locator('#send').isDisabled(),true);assert.equal(await viewer.locator('#disconnect').isDisabled(),true);
   await viewer.locator('#search').fill('loopback');await viewer.waitForFunction(()=>document.getElementById('terminal').textContent.includes('loopback'));
   await viewer.close();
  }
  await page.setViewportSize({width:390,height:844});await page.locator('#devices-toggle').click();assert.equal(await page.locator('#device-sidebar').isVisible(),true);await page.locator('#sidebar-backdrop').click({position:{x:370,y:20}});
  await page.screenshot({path:'build/workbench-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});
  await page.locator('#disconnect').click();await page.waitForFunction(()=>document.getElementById('state').textContent.includes('未连接'));assert.equal(await page.locator('#send').isDisabled(),true);
  assert.deepEqual(errors,[]);console.log('PASS: real UI loopback, line framing, shared view, pause, evidence, export, six styles, persistence, read-only, mobile, disconnect');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
