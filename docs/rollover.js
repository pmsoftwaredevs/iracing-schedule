/**
 * The previous-season rollover-matching flow, replacing what used to be an
 * emailed rollover recap (app/matcher.py + app/email.py's send_rollover_email):
 * there's no email or account anymore, so a pasted code from last season is
 * matched against the current season's championships (shared/series-match.js)
 * entirely client-side, right here on the page, and the results — matched,
 * unmatched, or "time slot changed" — render as inline banners instead.
 *
 * Special events are never auto-carried over (matching the old matcher's
 * explicit non-goal: events are the same by year, not season, so there's no
 * reliable season-to-season mapping to carry).
 */

import { resolveSelections, timeIndexForTime } from "./shared/resolve.js";
import { matchSeries } from "./shared/series-match.js";

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`failed to fetch ${path}: ${response.status}`);
  return response.json();
}

function slugForCode(code) {
  return `${code.slice(0, 4)}_s${code.slice(5)}`;
}

function appendBanner(container, text, isUnmatched) {
  const div = document.createElement("div");
  div.className = "rollover-banner" + (isUnmatched ? " is-unmatched" : "");
  div.textContent = text;
  container.appendChild(div);
}

export async function applyRollover({
  decoded,
  manifest,
  currentSeasonData,
  tickChampionship,
  tickEvent,
  recomputeCode,
  showMessage,
  bannerContainer,
}) {
  bannerContainer.innerHTML = "";

  let previousSeasonData;
  try {
    previousSeasonData = await fetchJson(`data/${slugForCode(manifest.previous)}.json`);
  } catch (err) {
    showMessage("Couldn't load last season's data to match your old selections. Try again.", true);
    return;
  }

  const resolvedOld = resolveSelections(decoded, previousSeasonData);
  if (resolvedOld.championships.length === 0 && resolvedOld.events.length === 0) {
    showMessage("That code didn't resolve to any selections from last season.", true);
    return;
  }

  const oldChampionships = resolvedOld.championships.map((c) => ({
    name: c.championship.name,
    timeslots: c.timeslots,
  }));
  const currentChampionships = currentSeasonData.championships.map((championship, index) => ({
    name: championship.name,
    index,
    championship,
  }));
  const { matched, unmatched } = matchSeries(oldChampionships, currentChampionships);

  for (const { old, new: current } of matched) {
    const timeslots = [];
    let timeChanged = false;
    for (const slot of old.timeslots) {
      if (timeIndexForTime(current.championship, slot.timeGmt) >= 0) {
        timeslots.push({ day: slot.day, timeGmt: slot.timeGmt });
        continue;
      }
      // That exact GMT time no longer exists for this championship (its cadence
      // changed) — fall back to the same day's first available time and flag it
      // rather than silently picking something the user never chose.
      const fallbackTimes = (current.championship.session_times_by_day || {})[String(slot.day)] || [];
      if (fallbackTimes.length > 0) {
        timeslots.push({ day: slot.day, timeGmt: fallbackTimes[0] });
        timeChanged = true;
      }
    }
    tickChampionship(current.index, timeslots);
    if (timeChanged) {
      appendBanner(bannerContainer, `${current.championship.name}: its usual time slot changed — double-check the day/time.`, false);
    }
  }

  for (const old of unmatched) {
    appendBanner(bannerContainer, `Not found this season, please re-pick: ${old.name}`, true);
  }

  for (const picked of resolvedOld.events) {
    appendBanner(bannerContainer, `Special events aren't carried over automatically — re-pick "${picked.event.name}" if you still want it.`, false);
  }

  recomputeCode();
  const total = resolvedOld.championships.length;
  const matchedCount = matched.length;
  showMessage(
    `Matched ${matchedCount} of ${total} championship${total === 1 ? "" : "s"} from last season — check the notes below and copy your new URL.`,
    matchedCount < total
  );
}
