/**
 * Renders each style at the Dive viewport and writes ../assets/<style>.png.
 *
 * Usage: npm install && npm run shoot
 * Starts Vite itself, so no separate dev server is needed.
 */
import { chromium } from 'playwright';
import { createServer } from 'vite';
import { mkdir } from 'node:fs/promises';

const STYLES = [
	'motherduck-default',
	'swiss-minimal',
	'paper',
	'editorial',
	'terminal',
];

const VIEWPORT = { width: 880, height: 620 };

const server = await createServer({ server: { port: 5174 } });
await server.listen();
const base = server.resolvedUrls.local[0].replace(/\/$/, '');

await mkdir('../assets', { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({
	viewport: VIEWPORT,
	deviceScaleFactor: 2,
});

const errors = [];
page.on('pageerror', (error) => errors.push(String(error)));
page.on('console', (message) => {
	if (message.type() === 'error') errors.push(message.text());
});

for (const style of STYLES) {
	await page.goto(`${base}/?style=${style}`, { waitUntil: 'networkidle' });
	// Tailwind's CDN build and Recharts both paint on a later frame.
	await page.waitForSelector('.recharts-surface');
	await page.waitForTimeout(400);
	await page.screenshot({ path: `../assets/${style}.png` });
	console.log(`wrote ../assets/${style}.png`);
}

await browser.close();
await server.close();

if (errors.length > 0) {
	console.error('Console/page errors:\n' + errors.join('\n'));
	process.exit(1);
}
