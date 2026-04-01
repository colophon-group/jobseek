/**
 * Puppeteer-based browser fetcher using @crawlee/puppeteer PuppeteerCrawler.
 *
 * Use this for JS-heavy sites where got-scraping returns empty/blocked content.
 * The Apify actor-node-puppeteer-chrome Docker image provides Chromium pre-installed.
 */
import { PuppeteerCrawler } from '@crawlee/puppeteer';

export interface BrowserFetchOptions {
  /** Milliseconds to wait after page load for dynamic content to render (default 2000). */
  waitMs?: number;
  /** Extra HTTP headers to send (e.g. Accept-Language). */
  extraHeaders?: Record<string, string>;
}

/**
 * Fetch a URL using a headless Chromium browser (Puppeteer).
 * Returns the page's full HTML after JS has rendered, or null on failure.
 */
export async function fetchPageWithPuppeteer(
  url: string,
  opts: BrowserFetchOptions = {},
): Promise<string | null> {
  const { waitMs = 2000, extraHeaders = {} } = opts;
  let html: string | null = null;

  const crawler = new PuppeteerCrawler({
    maxRequestRetries: 1,
    requestHandlerTimeoutSecs: 45,
    launchContext: {
      launchOptions: {
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu',
        ],
      },
    },
    async requestHandler({ page }) {
      if (Object.keys(extraHeaders).length > 0) {
        await page.setExtraHTTPHeaders(extraHeaders);
      }
      // Wait for the network to be mostly idle, then a bit more for late renders.
      await page.waitForNetworkIdle({ idleTime: 500 }).catch(() => null);
      await new Promise<void>(r => setTimeout(r, waitMs));
      html = await page.content();
    },
    failedRequestHandler({ request, log: crawlerLog }) {
      crawlerLog.warning(`Puppeteer fetch failed: ${request.url}`);
    },
  });

  await crawler.run([{ url }]);
  return html;
}
