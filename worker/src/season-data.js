/**
 * Fetches the manifest + season cache JSON live from the published GitHub Pages
 * site on every request, rather than bundling them into the Worker at deploy
 * time. Bundling would force a `wrangler deploy` every time the daily
 * cache-refresh workflow runs, coupling data freshness to code deploys for no
 * benefit — fetching live and letting Cloudflare's own edge cache
 * (`cf.cacheTtl`) absorb repeat traffic keeps the Worker always in sync with
 * whatever Actions just published, decoupled from how often the Worker's own
 * code changes.
 */

const CACHE_TTL_SECONDS = 300;

function slugForCode(seasonCode) {
  return `${seasonCode.slice(0, 4)}_s${seasonCode.slice(5)}`;
}

async function fetchJson(url) {
  const response = await fetch(url, { cf: { cacheTtl: CACHE_TTL_SECONDS, cacheEverything: true } });
  if (!response.ok) {
    throw new Error(`fetch ${url} failed: ${response.status}`);
  }
  return response.json();
}

export function fetchManifest(pagesBaseUrl) {
  return fetchJson(`${pagesBaseUrl}/data/manifest.json`);
}

export function fetchSeasonData(pagesBaseUrl, seasonCode) {
  return fetchJson(`${pagesBaseUrl}/data/${slugForCode(seasonCode)}.json`);
}
