/**
 * M3 Browser Runtime Bridge (Playwright).
 *
 * Implements Playwright-based session preparation and geo-testing.
 * Strictly adheres to LiveGuard safety & ethical invariants:
 * - NO credential scraping.
 * - NO residential proxy rotation for evasion.
 * - NO auto-login (manual login only).
 * - NO automated likes/comments/follows (passive actions only when explicitly opted-in).
 *
 * Emits newline-delimited JSON events over stdout:
 * {"event": "<name>", "payload": {...}, "timestamp": "..."}
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { chromium } from 'playwright';
import { formatPlaywrightProxy } from './proxy.js';

/**
 * Emit structured NDJSON event to stdout.
 *
 * @param {string} event Event name.
 * @param {Object} payload Event details.
 */
function emit(event, payload = {}) {
  const line = JSON.stringify({
    event,
    payload,
    timestamp: new Date().toISOString(),
  });
  process.stdout.write(`${line}\n`);
}

/**
 * Generate a random number from normal distribution with mean and stddev.
 * Box-Muller transform.
 *
 * @param {number} mean Distribution mean in ms.
 * @param {number} std Standard deviation in ms.
 * @returns {number} Gaussian sample.
 */
function gaussianRandom(mean = 800, std = 300) {
  let u = 0;
  let v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  const num = Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  return Math.max(100, Math.round(num * std + mean));
}

/**
 * Sleep helper.
 *
 * @param {number} ms Milliseconds to pause.
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Parse CLI arguments.
 *
 * @param {string[]} argv Process arguments.
 * @returns {Object} Parsed arguments.
 */
function parseArgs(argv) {
  const args = {
    profileId: 'default',
    action: 'check',
    headless: false,
    profileFile: '',
    sessionFile: '',
    timeout: 30000,
  };

  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--profile' && i + 1 < argv.length) {
      args.profileId = argv[++i];
    } else if (arg === '--action' && i + 1 < argv.length) {
      args.action = argv[++i];
    } else if (arg === '--headless') {
      args.headless = true;
    } else if (arg === '--profile-file' && i + 1 < argv.length) {
      args.profileFile = argv[++i];
    } else if (arg === '--session-file' && i + 1 < argv.length) {
      args.sessionFile = argv[++i];
    } else if (arg === '--timeout' && i + 1 < argv.length) {
      args.timeout = parseInt(argv[++i], 10);
    }
  }

  return args;
}

/**
 * Load browser profile from file or defaults.
 *
 * @param {string} filePath Path to profile JSON.
 * @param {string} profileId Fallback profile ID.
 * @returns {Object} Profile data.
 */
function loadProfile(filePath, profileId) {
  if (filePath && existsSync(filePath)) {
    try {
      const data = JSON.parse(readFileSync(filePath, 'utf-8'));
      return data;
    } catch (err) {
      emit('profile_parse_error', { path: filePath, error: err.message });
    }
  }

  return {
    id: profileId,
    userAgent:
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    viewport: { w: 1280, h: 720 },
    timezone: 'UTC',
    locale: 'en-US',
    allow_passive_feed_scroll: false,
    allow_interactions: false,
  };
}

/**
 * Execute passive warm-up routine if configured.
 *
 * @param {import('playwright').Page} page Active browser page.
 * @param {Object} profile Active profile.
 */
async function runWarmupIfEnabled(page, profile) {
  if (!profile.allow_passive_feed_scroll) {
    return;
  }

  emit('warmup_started', {
    profile_id: profile.id,
    allow_interactions: Boolean(profile.allow_interactions),
  });

  const actionCount = Math.floor(Math.random() * 6) + 5; // 5 to 10 actions
  for (let step = 1; step <= actionCount; step++) {
    const delayMs = gaussianRandom(800, 300);
    await sleep(delayMs);

    // Passive feed scroll (e.g. scroll down slightly)
    try {
      await page.evaluate(() => window.scrollBy({ top: 350, behavior: 'smooth' }));
      emit('warmup_action', {
        step,
        action: 'passive_scroll',
        delay_ms: delayMs,
      });
    } catch {
      // Ignore scroll errors on static or un-scrollable pages
    }
  }

  emit('warmup_completed', { total_actions: actionCount });
}

/**
 * Main execution function.
 */
async function main() {
  const args = parseArgs(process.argv);
  emit('m3_bridge_started', {
    profile_id: args.profileId,
    action: args.action,
    headless: args.headless,
  });

  const profile = loadProfile(args.profileFile, args.profileId);
  const proxyConfig = formatPlaywrightProxy(profile.proxy);

  let browser;
  try {
    browser = await chromium.launch({
      headless: args.headless,
      proxy: proxyConfig || undefined,
    });
  } catch (err) {
    emit('browser_launch_failed', { error: err.message });
    process.exit(1);
  }

  try {
    const contextOptions = {
      userAgent: profile.userAgent,
      viewport: profile.viewport
        ? { width: profile.viewport.w || 1280, height: profile.viewport.h || 720 }
        : { width: 1280, height: 720 },
      timezoneId: profile.timezone || 'UTC',
      locale: profile.locale || 'en-US',
    };

    const context = await browser.newContext(contextOptions);

    // Load existing session cookies if available
    let sessionValid = false;
    if (args.sessionFile && existsSync(args.sessionFile)) {
      try {
        const raw = readFileSync(args.sessionFile, 'utf-8');
        // Handle optional '# TODO' comment header lines
        const jsonStr = raw
          .split('\n')
          .filter((line) => !line.trim().startsWith('#'))
          .join('\n');
        const sessionData = JSON.parse(jsonStr);
        if (Array.isArray(sessionData.cookies) && sessionData.cookies.length > 0) {
          await context.addCookies(sessionData.cookies);
          sessionValid = true;
        }
      } catch (err) {
        emit('session_read_warning', { path: args.sessionFile, error: err.message });
      }
    }

    const page = await context.newPage();
    page.setDefaultTimeout(args.timeout);

    if (args.action === 'check') {
      const targetUrl = 'https://www.tiktok.com';
      emit('probe_started', { url: targetUrl, region: profile.region || 'US' });

      const startTime = Date.now();
      let geoResult = 'AVAILABLE';
      let latencyMs = 0;

      try {
        const response = await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });
        latencyMs = Date.now() - startTime;
        const status = response ? response.status() : 0;

        if (status === 429) {
          geoResult = 'RATE_LIMITED';
        } else if (status >= 400 && status < 500) {
          geoResult = 'UNAVAILABLE';
        } else if (status === 0 || status >= 500) {
          geoResult = 'PROBE_FAILED';
        } else {
          geoResult = 'AVAILABLE';
        }
      } catch (navErr) {
        latencyMs = Date.now() - startTime;
        geoResult = 'PROBE_FAILED';
        emit('probe_nav_error', { error: navErr.message });
      }

      // Check cookie validity if session cookies exist
      const cookies = await context.cookies();
      const hasAuthCookie = cookies.some(
        (c) => c.name === 'sessionid' || c.name === 'sid_guard' || c.name === 'sessionid_ss'
      );
      sessionValid = hasAuthCookie;

      await runWarmupIfEnabled(page, profile);

      emit('probe_completed', {
        region_probed: profile.region || 'US',
        geo_result: geoResult,
        latency_ms: latencyMs,
        session_valid: sessionValid,
        probe_url: targetUrl,
      });
    } else if (args.action === 'login') {
      const loginUrl = 'https://www.tiktok.com/login';
      emit('manual_login_waiting', {
        url: loginUrl,
        message:
          'Manual login required. Please log in using the browser window. Waiting for session...',
      });

      await page.goto(loginUrl, { waitUntil: 'domcontentloaded' });

      // Poll every 2 seconds for authentication cookie appearance or manual completion
      const pollIntervalMs = 2000;
      const maxWaitMs = args.timeout > 60000 ? args.timeout : 120000;
      const maxPolls = Math.floor(maxWaitMs / pollIntervalMs);
      let authenticated = false;

      for (let poll = 0; poll < maxPolls; poll++) {
        await sleep(pollIntervalMs);
        const currentCookies = await context.cookies();
        const found = currentCookies.some(
          (c) => c.name === 'sessionid' || c.name === 'sid_guard' || c.name === 'sessionid_ss'
        );

        if (found) {
          authenticated = true;
          emit('manual_login_success', { cookie_count: currentCookies.length });

          if (args.sessionFile) {
            const sessionPayload = {
              profile_id: profile.id,
              created_at: new Date().toISOString(),
              cookies: currentCookies,
            };
            const fileContent = `# TODO: encrypt in Phase 5\n${JSON.stringify(sessionPayload, null, 2)}\n`;
            writeFileSync(args.sessionFile, fileContent, { mode: 0o600 });
            emit('session_saved', {
              session_file: args.sessionFile,
              cookie_count: currentCookies.length,
            });
          }
          break;
        }
      }

      if (!authenticated) {
        emit('manual_login_timeout', {
          message: 'Manual login timed out without detecting session cookies.',
        });
      }
    }

    await browser.close();
    emit('m3_bridge_finished', { status: 'success' });
  } catch (err) {
    emit('m3_bridge_error', { error: err.message });
    if (browser) {
      await browser.close();
    }
    process.exit(1);
  }
}

main();
