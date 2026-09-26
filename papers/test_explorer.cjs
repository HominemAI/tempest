// Run with PLAYWRIGHT_MODULE and BROWSER_EXECUTABLE if Playwright isn't local.
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({executablePath: process.env.BROWSER_EXECUTABLE || undefined});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on('pageerror', error=>errors.push(error.message));
    await page.goto(pathToFileURL(path.join(__dirname,'explorer.html')).href);
    assert((await page.locator('.tablewrap').boundingBox()).y < 240);
    await page.locator('#body a').first().click();
    const box=await page.locator('#viewer').boundingBox();
    assert(Math.abs(box.x-(1440-box.width)/2)<2);
    assert(await page.locator('#viewer').evaluate(el=>el.open));
    const original=await page.locator('.reading').textContent();
    await page.locator('#wordInitials').click();
    assert((await page.locator('.word-initial').count())>0);
    assert.equal(await page.locator('.reading').textContent(),original);
    await page.locator('#docSearch').fill('global neuronal workspace');
    await page.waitForFunction(()=>document.querySelector('#docSearchStatus').textContent.startsWith('1 of'));
    await page.locator('#docSearch').press('Enter');
    assert((await page.locator('#docSearchStatus').textContent()).startsWith('2 of'));
    await page.locator('#docSearch').fill('');
    await page.waitForFunction(()=>!document.querySelector('mark.doc-match'));
    await page.locator('#goto').fill('1189'); await page.locator('#go').click();
    await page.evaluate(()=>{
      const walker=document.createTreeWalker(document.querySelector('.passage.target'),NodeFilter.SHOW_TEXT);
      let offset=0,start,end;
      while(walker.nextNode()) {
        const n=walker.currentNode;
        if(!start&&offset+n.length>10)start=[n,10-offset];
        if(offset+n.length>=70){end=[n,70-offset];break;}
        offset+=n.length;
      }
      const range=document.createRange();range.setStart(...start);range.setEnd(...end);
      getSelection().removeAllRanges();getSelection().addRange(range);
    });
    await page.locator('#highlightComment').fill('A saved research note');
    await page.locator('#saveHighlight').click();
    const record=await page.evaluate(()=>JSON.parse(localStorage.getItem('research-explorer-highlights-v1'))[0]);
    assert.equal(record.comment,'A saved research note');
    assert(record.segments[0].source_start.line>0);
    // Source ranges must select the exact original Markdown substring stored.
    assert(await page.evaluate(record=>record.segments.every(s=>currentDoc.text.slice(s.source_start_offset,s.source_end_offset)===s.source_text),record));
    await page.reload();await page.waitForSelector('mark.saved-highlight');
    await page.locator('mark.saved-highlight').first().click();
    assert.equal(await page.locator('#highlightComment').inputValue(),'A saved research note');
    if(process.env.SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'reader-comment.png')});
    await page.locator('#highlightComment').fill('Updated note');await page.locator('#saveHighlight').click();
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('research-explorer-highlights-v1'))[0].comment),'Updated note');
    for(let i=0;i<12;i++){await page.keyboard.press('Tab');assert(await page.evaluate(()=>document.querySelector('#viewer').contains(document.activeElement)));}
    if(process.env.SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'reader-desktop-final.png')});
    await page.keyboard.press('Escape');assert(await page.locator('#viewer').isHidden());
    if(process.env.SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'reader-table-final.png')});
    await page.setViewportSize({width:390,height:844});await page.locator('#body a').first().click();
    assert((await page.locator('#docSearch').boundingBox()).y<400);
    if(process.env.SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,'reader-mobile-final.png')});
    assert.deepEqual(errors,[]);
    console.log('Reader layout, formatting, search, highlight/comment persistence, source coordinates, and keyboard checks passed.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
