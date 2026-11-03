// Phone-sized touch tests for the phone app (swipes, hold-and-drag, editor layout).
// Usage: node phone_gestures.js <profileDir> <baseUrl> <apiKey> [shotDir]
const { chromium } = require("playwright");

async function main() {
  const [profile, base, key, shots] = process.argv.slice(2);
  const ctx = await chromium.launchPersistentContext(profile, {
    headless: true, viewport: { width: 402, height: 874 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("dialog", (d) => d.accept());
  // Behave like iOS (no vibration API) and count haptic ticks from the hidden switch.
  await ctx.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, "vibrate", { value: undefined, configurable: true });
    window.__haptics = 0;
    const click = HTMLLabelElement.prototype.click;
    HTMLLabelElement.prototype.click = function () { if (this.classList.contains("haptic")) window.__haptics++; return click.call(this); };
  });
  const cdp = await ctx.newCDPSession(page);
  const haptics = () => page.evaluate(() => window.__haptics);
  const out = { errors };
  const S = () => page.evaluate(() => window.__sidebar.state());
  const titles = () => page.$$eval("#list .card-title", (els) => els.map((e) => e.textContent));
  const box = (sel) => page.locator(sel).first().boundingBox();

  async function touch(x1, y1, x2, y2, { steps = 14, hold = 0 } = {}) {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: x1, y: y1 }] });
    if (hold) await page.waitForTimeout(hold);
    for (let i = 1; i <= steps; i++) {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove",
        touchPoints: [{ x: x1 + (x2 - x1) * i / steps, y: y1 + (y2 - y1) * i / steps }] });
      await page.waitForTimeout(12);
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await page.waitForTimeout(650);
  }
  async function cardBox(title) {
    return page.locator(`.card-wrap:has(.card-title:text-is("${title}"))`).boundingBox();
  }
  async function swipe(title, fraction) {
    const b = await cardBox(title);
    const y = b.y + 18, x = fraction < 0 ? b.x + b.width - 20 : b.x + 20;   // start from the far side
    await touch(x, y, x + b.width * fraction, y);
  }

  await page.goto(base + "/index.html");
  // the local test server is plain http, which the setup screen rightly refuses: store it directly
  await page.evaluate(([b, k]) => { localStorage.setItem("sidebar.phone.v1", JSON.stringify({ url: b, key: k })); }, [base, key]);
  await page.reload();
  await page.fill("input[type=email]", "me@example.com");
  await page.fill("input[type=password]", "correct horse");
  await page.click("button.primary");
  await page.waitForSelector(".card-title:text-is('Alpha')", { timeout: 15000 });
  out.start = await titles();
  if (shots) await page.screenshot({ path: shots + "/g-list.png" });

  // header row: category and priority tags on the same line as the title
  const t = await box(".card-wrap:has(.card-title:text-is('Alpha')) .card-title");
  const tag = await box(".card-wrap:has(.card-title:text-is('Alpha')) .tags .badge");
  out.tagsInline = Math.abs((t.y + t.height / 2) - (tag.y + tag.height / 2)) < 12;
  // no delete controls in the list
  out.deleteInList = await page.$$eval("#list button", (bs) => bs.some((b) => /delete/i.test(b.textContent)));

  // partial left swipe: nothing (and no haptic); full left swipe: archive (one tick at the threshold)
  let h0 = await haptics();
  await swipe("Alpha", -0.3);
  out.partialRight = (await titles()).includes("Alpha");      // still in the list
  out.hapticsPartial = (await haptics()) - h0;
  h0 = await haptics();
  await swipe("Alpha", -0.75);
  out.hapticsFull = (await haptics()) - h0;
  out.afterFullRight = await titles();
  out.alphaArchived = Object.values((await S()).cards).find((c) => c.title === "Alpha").archived;

  // partial right swipe: nothing; full right swipe: star
  await swipe("Beta", 0.3);
  out.betaAfterPartial = Object.values((await S()).cards).find((c) => c.title === "Beta").starred;
  await swipe("Beta", 0.75);
  out.betaStarred = Object.values((await S()).cards).find((c) => c.title === "Beta").starred;
  out.starVisible = await page.locator(".card-wrap:has(.card-title:text-is('Beta')) .star").count();
  if (shots) await page.screenshot({ path: shots + "/g-starred.png" });

  // hold and drag Delta above Beta
  const before = await titles();
  const d = await cardBox("Delta"), bb = await cardBox("Beta");
  h0 = await haptics();
  await touch(d.x + 60, d.y + 20, d.x + 60, bb.y + 4, { hold: 500, steps: 20 });
  out.hapticsDrag = (await haptics()) - h0;
  out.reorder = { before, after: await titles() };

  // a vertical swipe on a card must not archive, star or reorder
  const g = await cardBox("Gamma");
  await touch(g.x + 60, g.y + 20, g.x + 70, g.y - 120, { steps: 10 });
  out.afterScroll = await titles();

  // editor layout
  await page.click(".card-title:text-is('Gamma')");
  await page.waitForSelector(".sheet.open");
  await page.waitForTimeout(400);
  const back = await box(".sheet-bar .back"), done = await box(".sheet-bar .primary-pill");
  out.backLeft = back.x < 60 && /Back/.test(await page.textContent(".sheet-bar .back"));
  out.doneRight = done.x + done.width > 402 - 40 && (await page.textContent(".sheet-bar .primary-pill")) === "Done";
  const due = await box(".meta input[type=date]"), rem = await box(".meta input[type=datetime-local]");
  out.datesOverlap = !(due.y + due.height <= rem.y || rem.y + rem.height <= due.y)
    && !(due.x + due.width <= rem.x || rem.x + rem.width <= due.x);
  out.datesFit = due.x + due.width <= 402 && rem.x + rem.width <= 402;
  out.markDoneGone = (await page.locator(".sheet :text('Mark done')").count()) === 0;
  // due date + time side by side, not overlapping; time only usable once a date is set
  const tm = await box(".meta input[type=time]");
  out.timeDisabledFirst = await page.locator(".meta input[type=time]").isDisabled();
  out.dateTimeOverlap = !(due.x + due.width <= tm.x || tm.x + tm.width <= due.x) &&
    !(due.y + due.height <= tm.y || tm.y + tm.height <= due.y);
  out.dateTimeFit = tm.x + tm.width <= 402;
  const tomorrow = await page.evaluate(() => { const d = new Date(); d.setDate(d.getDate() + 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; });
  await page.fill(".meta input[type=date]", tomorrow);
  await page.dispatchEvent(".meta input[type=date]", "change");
  await page.fill(".meta input[type=time]", "08:15");
  await page.dispatchEvent(".meta input[type=time]", "change");
  out.deleteAtBottom = (await box(".delete-note")).y > due.y;
  if (shots) await page.screenshot({ path: shots + "/g-editor.png", fullPage: true });
  await page.click(".sheet-bar .primary-pill");
  await page.waitForTimeout(500);
  out.gammaBadge = await page.locator(".card-wrap:has(.card-title:text-is('Gamma')) .badges").textContent();

  // new note left empty with Back: dropped; new note with a title: kept
  const n0 = (await titles()).length;
  await page.click(".fab");
  await page.click(".types button:has-text('Note')");
  await page.waitForSelector(".sheet.open");
  await page.click(".sheet-bar .back");
  await page.waitForTimeout(500);
  out.emptyDropped = (await titles()).length === n0;
  await page.click(".fab");
  await page.click(".types button:has-text('Checklist')");
  await page.waitForSelector(".sheet.open");
  await page.fill(".sheet .title", "Packing");
  await page.click(".sheet-bar .primary-pill");
  await page.waitForTimeout(500);
  out.newKept = (await titles()).includes("Packing");

  // delete from inside a note
  await page.click(".card-title:text-is('Packing')");
  await page.waitForSelector(".sheet.open");
  await page.click(".delete-note");
  await page.waitForTimeout(500);
  out.deleted = !(await titles()).includes("Packing");

  await page.waitForFunction(() => Object.keys(window.__sidebar.state().queue.cards).length === 0, null, { timeout: 15000 })
    .catch(() => {});
  await page.evaluate(() => window.__sidebar.syncNow());
  await page.waitForTimeout(800);
  out.queueLeft = Object.keys((await S()).queue.cards).length;
  out.noStarColumn = !!(await S()).noStarColumn;
  await ctx.close();
  console.log(JSON.stringify(out));
}
main().catch((e) => { console.error(e); process.exit(1); });
