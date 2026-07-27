/**
 * Ports app/matcher.py's match_series logic: matches a previous season's
 * championships against the current season's by exact name, then a
 * normalized/loose comparison as a fallback (series persist across seasons
 * under stable-ish names, e.g. "GT3 Fixed"). Used entirely client-side by the
 * paste-code "manage" flow (see docs/rollover.js) to replace what used to be an
 * emailed rollover recap — there's no email anymore, so unmatched/matched
 * results are rendered inline on the page instead.
 *
 * Callers pass plain `{name, ...}` objects; whatever else is on each object
 * (an index, timeslots, a championship reference) is carried through untouched
 * in the returned `matched`/`unmatched` entries.
 */

function normalize(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

export function matchSeries(oldSeries, newSeries) {
  const byExactName = new Map(newSeries.map((s) => [s.name, s]));
  const byNormalizedName = new Map();
  for (const s of newSeries) {
    const key = normalize(s.name);
    if (!byNormalizedName.has(key)) byNormalizedName.set(key, []);
    byNormalizedName.get(key).push(s);
  }

  const matched = [];
  const unmatched = [];
  for (const old of oldSeries) {
    let candidate = byExactName.get(old.name);
    if (!candidate) {
      const candidates = byNormalizedName.get(normalize(old.name)) || [];
      candidate = candidates.length === 1 ? candidates[0] : undefined;
    }
    if (candidate) {
      matched.push({ old, new: candidate });
    } else {
      unmatched.push(old);
    }
  }
  return { matched, unmatched };
}
