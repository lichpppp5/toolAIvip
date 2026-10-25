/**
 * M3 Proxy Configuration Helper for Playwright.
 *
 * Formats proxy configurations for Playwright browser launch.
 * NO residential proxy rotation or ban evasion.
 * Explicitly used for geographic availability testing only.
 */

import { URL } from 'node:url';

/**
 * Format a proxy configuration object for Playwright.
 *
 * @param {Object|string|null} proxy Input proxy descriptor or URL.
 * @returns {Object|null} Playwright proxy configuration or null if not set.
 */
export function formatPlaywrightProxy(proxy) {
  if (!proxy) {
    return null;
  }

  const rawUrl = typeof proxy === 'string' ? proxy : proxy.url;
  if (!rawUrl) {
    return null;
  }

  try {
    const parsed = new URL(rawUrl);
    const config = {
      server: `${parsed.protocol}//${parsed.hostname}${parsed.port ? `:${parsed.port}` : ''}`,
    };

    if (parsed.username) {
      config.username = decodeURIComponent(parsed.username);
    }
    if (parsed.password) {
      config.password = decodeURIComponent(parsed.password);
    }

    return config;
  } catch (err) {
    throw new Error(`Invalid proxy URL '${rawUrl}': ${err.message}`);
  }
}
