// Drives the phone app in headless Chromium for tests/test_phone_app.py.
// Usage: node phone_e2e.js <phase> <profileDir> <baseUrl> <apiKey>
const { chromium } = require("playwright");

async function main() {
  const [phase, profile, base, key] = process.argv.slice(2);
  const ctx = await chromium.launchPersistentContext(profile, { headless: true, viewport: { width: 390, height: 844 } });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("dialog", (d) => d.accept(d.type() === "prompt" ? "Phone category" : undefined));
  await page.goto(base + "/index.html");
  const out = { errors };
  const waitQueueEmpty = () => page.waitForFunction(() => {
    const s = window.__sidebar.state();
    return Object.keys(s.queue.cards).length === 0 && Object.keys(s.queue.boards).length === 0;
  }, null, { timeout: 15000 });

  if (phase === "a") {
    await page.fill("input[placeholder^='https://']", base.replace("http://", "https://"));  // validated as https
    await page.fill("input[placeholder^='Publishable']", key);
    await page.click("text=Continue");
    // point the stored URL at the local http test server
    await page.evaluate((b) => { const s = JSON.parse(localStorage.getItem("sidebar.phone.v1")); s.url = b;
      localStorage.setItem("sidebar.phone.v1", JSON.stringify(s)); }, base);
    await page.reload();
    await page.fill("input[type=email]", "me@example.com");
    await page.fill("input[type=password]", "wrong");
    await page.click("button.primary");
    await page.waitForSelector("text=Invalid login credentials");
    out.badLogin = true;
    await page.fill("input[type=password]", "correct horse");
    await page.click("button.primary");
    await page.waitForSelector(".card-title:has-text('Groceries')", { timeout: 15000 });
    out.titles = await page.$$eval(".card-title", (els) => els.map((e) => e.textContent));
    out.chips = await page.$$eval(".chip", (els) => els.map((e) => e.textContent));
    if (process.env.SHOT_DIR) await page.screenshot({ path: process.env.SHOT_DIR + "/phone-list.png" });
    // tick the first checklist item straight from the list
    await page.click(".card:has(.card-title:has-text('Groceries')) .item input");
    // new note via the + button
    await page.click(".fab");
    await page.click(".types button:has-text('Note')");
    await page.fill(".sheet .title", "From my phone");
    await page.fill(".sheet textarea", "typed on the bus");
    if (process.env.SHOT_DIR) await page.screenshot({ path: process.env.SHOT_DIR + "/phone-editor.png" });
    await page.click(".sheet-bar button:has-text('Done')");
    await waitQueueEmpty();
    // XSS check: a title containing markup must render as text
    out.markupSafe = await page.$$eval(".card-title", (els) => els.some((e) => e.textContent.includes("<img")) &&
      !document.querySelector(".card-title img"));
  } else if (phase === "b") {
    await page.waitForSelector(".card-title", { timeout: 15000 });
    await page.evaluate(() => window.__sidebar.syncNow());
    await page.waitForSelector(".card-title:has-text('Edited on PC')", { timeout: 15000 });
    out.titles = await page.$$eval(".card-title", (els) => els.map((e) => e.textContent));
    // archive one card, delete another
    await page.click(".card-title:has-text('Edited on PC')");
    await page.click(".sheet button:has-text('Mark done')");
    await page.click(".card-title:has-text('From my phone')");
    await page.click(".sheet-bar button:has-text('Delete')");
    await waitQueueEmpty();
    await page.click(".views button[data-view=archive]");
    out.archive = await page.$$eval(".card-title", (els) => els.map((e) => e.textContent));
  }
  await ctx.close();
  console.log(JSON.stringify(out));
}
main().catch((e) => { console.error(e); process.exit(1); });
