/**
 * Live `.ics` endpoint: GET /calendar/{code}.ics — decodes the code (see
 * shared/calendar-code.js), resolves it against the current/previous season's
 * cache (see shared/resolve.js), and returns the generated calendar (see
 * ics.js). There is no database and no per-user state: every request is
 * decoded fresh from the code alone.
 *
 * Always returns 200 with a valid calendar, even for a malformed code or a
 * season outside the 2-season retention window (empty calendar in that case) —
 * a broken subscription in a calendar app is worse than an empty one. See
 * ARCHITECTURE.md's "always decode to empty, never error" contract.
 */

import { decodeCalendarCode } from "../../shared/calendar-code.js";
import { resolveSelections } from "../../shared/resolve.js";
import { buildCalendar } from "./ics-format.js";
import { buildIcs } from "./ics.js";
import { fetchManifest, fetchSeasonData } from "./season-data.js";

const CALENDAR_PATH_RE = /^\/calendar\/(.+)\.ics$/;

async function buildResponseBody(code, pagesBaseUrl) {
  const manifest = await fetchManifest(pagesBaseUrl);
  const decoded = decodeCalendarCode(code, manifest);

  let resolved = { championships: [], events: [] };
  if (decoded.inRetentionWindow) {
    const seasonData = await fetchSeasonData(pagesBaseUrl, decoded.seasonCode);
    resolved = resolveSelections(decoded, seasonData);
  }
  return buildIcs(resolved, decoded.seasonCode || "unknown");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const match = CALENDAR_PATH_RE.exec(url.pathname);
    if (!match) {
      return new Response("Not found. Use /calendar/{code}.ics", { status: 404 });
    }

    let ics;
    try {
      ics = await buildResponseBody(match[1], env.PAGES_BASE_URL);
    } catch (err) {
      // Fetching the season cache failed (Pages down, network blip, bad data) —
      // still return a valid, empty calendar rather than a broken subscription.
      ics = buildCalendar({ calname: "iRacing Calendar", eventLines: [] });
    }

    return new Response(ics, {
      headers: {
        "Content-Type": "text/calendar; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
      },
    });
  },
};
