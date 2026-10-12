import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

import {
  validateOfficialTextIdentity,
  waitForCongressPage,
} from "../enrich_congress_text_relevance_pages.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

test("official-text identity binds the exact bill or amendment path", () => {
  const bill = {
    canonical_key: "119:hr:42",
    canonical_url:
      "https://www.congress.gov/bill/119th-congress/house-bill/42",
    display_number: "H.R. 42",
  };
  const verified = validateOfficialTextIdentity(bill, {
    loaded_url:
      "https://www.congress.gov/bill/119th-congress/house-bill/42/text",
    page_title: "Text - H.R. 42 - 119th Congress",
  });
  assert.equal(verified.verified, true);
  assert.equal(verified.page_title_contains_display_number, true);
  assert.throws(
    () =>
      validateOfficialTextIdentity(bill, {
        loaded_url:
          "https://www.congress.gov/bill/119th-congress/house-bill/43/text",
        page_title: "Text - H.R. 43 - 119th Congress",
      }),
    /identity mismatch/,
  );
});

test("readiness waits for settled content and rejects challenge chrome", async () => {
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <main id="main">Shown Here:</main>
      <script>
        setTimeout(() => {
          document.querySelector('#main').textContent =
            'Shown Here:\\nThis section amends the Federal Credit Union Act.';
        }, 150);
      </script>
    `);
    await waitForCongressPage(page, "#main, main", true, 3_000);
    assert.match(await page.locator("#main").innerText(), /Federal Credit Union Act/);

    await page.setContent(`
      <main id="main">Shown Here:\nStale bill text</main>
      <div>Cloudflare verifying you are human</div>
    `);
    await assert.rejects(
      waitForCongressPage(page, "#main, main", true, 800),
      /did not expose/,
    );
  } finally {
    await browser.close();
  }
});
