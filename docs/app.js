/**
 * Renders the championship/special-event picker entirely client-side from
 * data/manifest.json + data/{season}.json (see tools/build_cache.py), and keeps
 * a deterministic calendar code (see shared/calendar-code.js) in sync with
 * whatever's checked on the page — there is no submit button and no server: the
 * code (and the subscribe URL built from it) just recomputes on every change.
 *
 * "Managing" an existing calendar is the paste-code box: decode a pasted code
 * (or full subscribe URL) client-side and pre-tick the matching selections so
 * they can be edited and re-shared as a new code. shared/series-match.js's
 * rollover-matching flow (for a code from last season) hooks into
 * `applyCodeToCurrentSeason`'s `needsRollover` branch.
 */

import { encodeCalendarCode, decodeCalendarCode, parseCalendarCode } from "./shared/calendar-code.js";
import { resolveSelections, timeIndexForTime } from "./shared/resolve.js";
import { applyRollover } from "./rollover.js";

const config = window.IRCAL_CONFIG || {};
const WORKER_BASE_URL = config.workerBaseUrl || "http://localhost:8787";
const ADS_PUBLISHER_ID = config.adsPublisherId || "";

// Week order matches the GMT week (Tuesday rollover) and the old
// Timeslot.day_of_week convention (0=Monday..6=Sunday).
const DAY_LABELS = [
  [1, "Tuesday"], [2, "Wednesday"], [3, "Thursday"], [4, "Friday"],
  [5, "Saturday"], [6, "Sunday"], [0, "Monday"],
];

// iRacing's own per-license colors — mirrors pipeline/licenses.py so the ladder
// order/colors live in exactly one place conceptually, just duplicated across the
// Python/JS boundary the same way DAY_LABELS already was in the old templates.
const LICENSE_TIERS = [
  { code: "R", name: "Rookie", color: "#E1251B", textColor: "#ffffff" },
  { code: "D", name: "Class D", color: "#FF6600", textColor: "#ffffff" },
  { code: "C", name: "Class C", color: "#FFCC00", textColor: "#1a1d1a" },
  { code: "B", name: "Class B", color: "#217C06", textColor: "#ffffff" },
  { code: "A", name: "Class A", color: "#006EFF", textColor: "#ffffff" },
  { code: "P", name: "Pro/World Champion", color: "#000000", textColor: "#ffffff" },
];
const LICENSE_BY_CODE = Object.fromEntries(LICENSE_TIERS.map((t) => [t.code, t]));

// Track-type categories parsed from the schedule PDF (see
// pipeline/parsers/schedule_pdf.py's CATEGORY_HEADER_RE). Icon paths are
// iRacing's own, lifted from members-ng's rendered category pickers.
// "UNRANKED" is deliberately not a category here — it's the PDF's own catch-all
// for the handful of series that don't count toward license/Safety Rating, and
// is instead surfaced as the absence of the "ranked" badge (see isRankedSeries).
const CATEGORY_TIERS = [
  {
    code: "OVAL",
    name: "Oval",
    icon: '<svg viewBox="0 0 24 24" focusable="false" class="category-svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M18 7.5H6C4.34315 7.5 3 8.84315 3 10.5V11.2918C3 12.4281 3.64201 13.4669 4.65836 13.9751L7.30426 15.298C10.2603 16.776 13.7397 16.776 16.6957 15.298L19.3416 13.9751C20.358 13.4669 21 12.4281 21 11.2918V10.5C21 8.84315 19.6569 7.5 18 7.5ZM6 4.5H18C21.3137 4.5 24 7.18629 24 10.5V11.2918C24 13.5644 22.716 15.642 20.6833 16.6584L18.0374 17.9813C14.2368 19.8816 9.76324 19.8816 5.96262 17.9813L3.31672 16.6584C1.28401 15.642 0 13.5644 0 11.2918V10.5C0 7.18629 2.68629 4.5 6 4.5Z" fill="currentColor"></path></svg>',
  },
  {
    code: "SPORTS CAR",
    name: "Sports Car",
    icon: '<svg viewBox="0 0 24 24" focusable="false" class="category-svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M22.5 10.5H21.6215L20.1215 9H21C21.8284 9 22.5 8.32843 22.5 7.5H18.6215L18.0002 6.87868C17.4376 6.31607 16.6746 6 15.8789 6H8.12155C7.3259 6 6.56284 6.31607 6.00023 6.87868L5.37891 7.5H1.5C1.5 8.32843 2.17157 9 3 9H3.87891L2.37891 10.5H1.5C0.671573 10.5 0 11.1716 0 12V16.5C0 17.3284 0.671573 18 1.5 18H3C3.82843 18 4.5 17.3284 4.5 16.5H19.5C19.5 17.3284 20.1716 18 21 18H22.5C23.3284 18 24 17.3284 24 16.5V12C24 11.1716 23.3284 10.5 22.5 10.5ZM19.5002 10.5H4.50023L7.06066 7.93934C7.34196 7.65803 7.7235 7.5 8.12132 7.5H15.8787C16.2765 7.5 16.658 7.65804 16.9393 7.93934L19.5002 10.5ZM8.56066 12.4393L7.81066 13.1893C7.61175 13.3883 7.5 13.658 7.5 13.9393C7.5 14.5251 7.97487 15 8.56066 15H15.4393C16.0251 15 16.5 14.5251 16.5 13.9393C16.5 13.658 16.3883 13.3883 16.1893 13.1893L15.4393 12.4393C15.158 12.158 14.7765 12 14.3787 12H9.62132C9.2235 12 8.84197 12.158 8.56066 12.4393ZM4.79289 13.2071C4.60536 13.3946 4.351 13.5 4.08579 13.5H3V12H6L4.79289 13.2071ZM21 13.5H19.9142C19.649 13.5 19.3946 13.3946 19.2071 13.2071L18 12H21V13.5Z" fill="currentColor"></path></svg>',
  },
  {
    code: "FORMULA CAR",
    name: "Formula Car",
    icon: '<svg viewBox="0 0 24 24" focusable="false" class="category-svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M8.95381 9H7.5C6.67157 9 6 9.67157 6 10.5V12.5L4.5 13V12C4.5 11.1716 3.82843 10.5 3 10.5H1.5C0.671573 10.5 0 11.1716 0 12V16.5C0 17.3284 0.671573 18 1.5 18H3C3.82843 18 4.5 17.3284 4.5 16.5V14.5L6.03931 13.9869C6.27193 15.4122 7.50893 16.5 9 16.5H9.85714L10.0714 18H4.5C4.5 18.8284 5.17157 19.5 6 19.5H10.2857L10.2879 19.5151C10.4096 20.3671 11.1393 21 12 21C12.8607 21 13.5904 20.3671 13.7121 19.5151L13.7143 19.5H18C18.8284 19.5 19.5 18.8284 19.5 18H13.9286L14.1429 16.5H15C16.4911 16.5 17.7281 15.4122 17.9607 13.9869L19.5 14.5V16.5C19.5 17.3284 20.1716 18 21 18H22.5C23.3284 18 24 17.3284 24 16.5V12C24 11.1716 23.3284 10.5 22.5 10.5H21C20.1716 10.5 19.5 11.1716 19.5 12V13L18 12.5V10.5C18 9.67157 17.3284 9 16.5 9H15.0461L14.6711 7.5H19.5C20.3284 7.5 21 6.82843 21 6H14.2961L14.2762 5.92025C14.0675 5.08556 13.3176 4.5 12.4572 4.5H11.5428C10.6824 4.5 9.93242 5.08556 9.72375 5.92025L9.70381 6H3C3 6.82843 3.67157 7.5 4.5 7.5H9.32881L8.95381 9ZM15 10.5H16.5V13.5C16.5 14.3284 15.8284 15 15 15H14.3571L15 10.5ZM10.5 9L13.5 9L12.821 6.28405C12.7793 6.11711 12.6293 6 12.4572 6L11.5428 6C11.3707 6 11.2207 6.11711 11.179 6.28405L10.5 9ZM9 10.5H7.5V13.5C7.5 14.3284 8.17157 15 9 15H9.64286L9 10.5Z" fill="currentColor"></path></svg>',
  },
  {
    code: "DIRT OVAL",
    name: "Dirt Oval",
    icon: '<svg viewBox="0 0 24 24" focusable="false" class="category-svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M8 3H16C19.7712 3 21.6569 3 22.8284 4.17157C24 5.34315 24 7.22876 24 11V13C24 16.7712 24 18.6569 22.8284 19.8284C21.6569 21 19.7712 21 16 21H8C4.22876 21 2.34315 21 1.17157 19.8284C0 18.6569 0 16.7712 0 13V11C0 7.22876 0 5.34315 1.17157 4.17157C2.34315 3 4.22876 3 8 3ZM9 9H15C16.6569 9 18 10.3431 18 12C18 13.6569 16.6569 15 15 15H9C7.34315 15 6 13.6569 6 12C6 10.3431 7.34315 9 9 9ZM15 6H9C5.68629 6 3 8.68629 3 12C3 15.3137 5.68629 18 9 18H15C18.3137 18 21 15.3137 21 12C21 8.68629 18.3137 6 15 6Z" fill="currentColor"></path></svg>',
  },
  {
    code: "DIRT ROAD",
    name: "Dirt Road",
    icon: '<svg viewBox="0 0 24 24" focusable="false" class="category-svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M8 3H16C19.7712 3 21.6569 3 22.8284 4.17157C24 5.34315 24 7.22876 24 11V13C24 16.7712 24 18.6569 22.8284 19.8284C21.6569 21 19.7712 21 16 21H8C4.22876 21 2.34315 21 1.17157 19.8284C0 18.6569 0 16.7712 0 13V11C0 7.22876 0 5.34315 1.17157 4.17157C2.34315 3 4.22876 3 8 3ZM6 18H3V9C3 7.34315 4.34315 6 6 6H10.5C12.1569 6 13.5 7.34315 13.5 9V13C13.5 14.1046 14.3954 15 15.5 15H16C17.1046 15 18 14.1046 18 13V6H21V15C21 16.6569 19.6569 18 18 18H13.5C11.8431 18 10.5 16.6569 10.5 15V11C10.5 9.89543 9.60457 9 8.5 9H8C6.89543 9 6 9.89543 6 11V18Z" fill="currentColor"></path></svg>',
  },
];
const CATEGORY_BY_CODE = Object.fromEntries(CATEGORY_TIERS.map((t) => [t.code, t]));

// Every series is ranked (counts toward license/SR) except the handful the PDF
// groups under its own "UNRANKED" heading — see CATEGORY_TIERS' note above.
function isRankedSeries(championship) {
  return championship.category !== "UNRANKED";
}

const nowIso = new Date().toISOString().slice(0, 10);

const championshipsPanel = document.getElementById("championships-panel");
const eventsPanel = document.getElementById("events-panel");
const seriesCardTemplate = document.getElementById("series-card-template");
const slotRowTemplate = document.getElementById("slot-row-template");
const eventCardTemplate = document.getElementById("event-card-template");
const resultUrlEl = document.getElementById("result-url");
const copyUrlButton = document.getElementById("copy-url-button");
const subscribeUrlBox = document.getElementById("subscribe-url-box");
const filterInput = document.getElementById("filter-input");
const filterClear = document.getElementById("filter-clear");
const timezoneSelect = document.getElementById("timezone-select");
const licenseFilterContainer = document.getElementById("license-filter");
const categoryFilterContainer = document.getElementById("category-filter");
const pasteBox = document.getElementById("paste-code-box");
const pasteInput = document.getElementById("paste-code-input");
const pasteMessage = document.getElementById("paste-code-message");
const cookieConsentBanner = document.getElementById("cookie-consent-banner");
const filterToggle = document.getElementById("filter-toggle");
const filtersRow = document.getElementById("filters-row");
const tzToggle = document.getElementById("tz-toggle");
const tzPicker = document.getElementById("tz-picker");
const calendarCodeToggle = document.getElementById("calendar-code-toggle");

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`failed to fetch ${path}: ${response.status}`);
  return response.json();
}

async function loadCurrentSeason() {
  const manifest = await fetchJson("data/manifest.json");
  const seasonData = await fetchJson(`data/${slugForCode(manifest.current)}.json`);
  return { manifest, seasonData };
}

function slugForCode(code) {
  return `${code.slice(0, 4)}_s${code.slice(5)}`;
}

function formatSeasonLabel(season) {
  return `${season.year} Season ${season.quarter}`;
}

// ---- Rendering helpers ----

function availableDays(sessionTimesByDay) {
  const activeDays = Object.keys(sessionTimesByDay || {}).filter((d) => (sessionTimesByDay[d] || []).length > 0);
  if (activeDays.length === 0) return DAY_LABELS;
  const activeSet = new Set(activeDays.map(Number));
  return DAY_LABELS.filter(([value]) => activeSet.has(value));
}

function formatDuration(minutes) {
  if (minutes === null || minutes === undefined) return null;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours && mins) return `${hours}h ${mins}m`;
  if (hours) return `${hours}h`;
  return `${mins}m`;
}

function currentOrNextWeek(weeks) {
  if (!weeks.length) return null;
  const upcoming = weeks.filter((w) => w.date_end >= nowIso);
  if (upcoming.length) return upcoming.reduce((a, b) => (a.week_number < b.week_number ? a : b));
  return weeks.reduce((a, b) => (a.week_number > b.week_number ? a : b));
}

function buildChampionshipCard(championship, index) {
  const card = seriesCardTemplate.content.firstElementChild.cloneNode(true);
  card.dataset.championshipIndex = String(index);
  card.dataset.license = championship.license_level || "";
  // "UNRANKED" is normalized away here too: it isn't a real category, so the
  // category filter should never gate on it (same as no category at all).
  card.dataset.category = championship.category === "UNRANKED" ? "" : (championship.category || "");

  const checkbox = card.querySelector(".series-checkbox");
  checkbox.dataset.championshipIndex = String(index);
  checkbox.id = `championship-${index}`;
  card.querySelector("label").setAttribute("for", checkbox.id);

  const tier = LICENSE_BY_CODE[championship.license_level];
  if (tier) {
    const badge = card.querySelector(".license-badge");
    badge.hidden = false;
    badge.textContent = tier.code;
    badge.title = tier.name;
    badge.style.setProperty("--badge-bg", tier.color);
    badge.style.setProperty("--badge-fg", tier.textColor);
  }

  const categoryTier = CATEGORY_BY_CODE[championship.category];
  if (categoryTier) {
    const categoryIcon = card.querySelector(".category-icon");
    categoryIcon.hidden = false;
    categoryIcon.innerHTML = categoryTier.icon;
    categoryIcon.title = categoryTier.name;
  }

  if (isRankedSeries(championship)) {
    const ranked = card.querySelector(".ranked-badge");
    ranked.hidden = false;
    ranked.title = "Ranked — counts toward license and Safety Rating";
  }

  const nameEl = championship.link_url ? card.querySelector(".series-link") : card.querySelector(".series-plain");
  nameEl.hidden = false;
  nameEl.textContent = championship.name;
  if (championship.link_url) nameEl.href = championship.link_url;

  const durationLabel = formatDuration(championship.typical_session_duration_minutes);
  if (durationLabel) {
    const durationEl = card.querySelector(".series-duration");
    durationEl.hidden = false;
    durationEl.textContent = `~${durationLabel} / session`;
  }

  const weeks = championship.weeks || [];
  const current = currentOrNextWeek(weeks);
  if (current) {
    const scheduleEl = card.querySelector(".schedule");
    scheduleEl.hidden = false;
    scheduleEl.querySelector(".current-track-text").textContent =
      `W${current.week_number} · ${current.date_start} · ${current.track_name}`;
    scheduleEl.querySelector(".current-track").classList.toggle("is-final", current.date_end < nowIso);
    const list = scheduleEl.querySelector(".schedule-list");
    for (const week of weeks) {
      const li = document.createElement("li");
      if (week.date_start <= nowIso && nowIso <= week.date_end) li.classList.add("is-current");
      const weekNum = document.createElement("span");
      weekNum.className = "week-num";
      weekNum.textContent = `W${week.week_number}`;
      const weekDate = document.createElement("span");
      weekDate.className = "week-date";
      weekDate.textContent = week.date_start;
      const weekTrack = document.createElement("span");
      weekTrack.className = "week-track";
      weekTrack.textContent = week.track_name;
      li.append(weekNum, weekDate, weekTrack);
      list.appendChild(li);
    }
  }

  card.querySelector(".slots-container").dataset.championshipIndex = String(index);
  card.querySelector(".add-slot").dataset.championshipIndex = String(index);

  return card;
}

function buildEventCard(event, index) {
  const card = eventCardTemplate.content.firstElementChild.cloneNode(true);
  card.dataset.eventIndex = String(index);

  const checkbox = card.querySelector(".event-checkbox");
  checkbox.dataset.eventIndex = String(index);
  checkbox.id = `event-${index}`;
  card.querySelector("label").setAttribute("for", checkbox.id);

  const nameEl = card.querySelector(".series-name");
  nameEl.textContent = event.name;
  if (event.date_end < nowIso) nameEl.closest("label").classList.add("event-past");

  let dateRange = event.date_start;
  if (event.date_end !== event.date_start) dateRange += ` – ${event.date_end}`;
  let hint = `(${dateRange})`;
  if (event.track_name) hint += ` — ${event.track_name}`;
  if (event.car_class) hint += ` — ${event.car_class}`;
  card.querySelector(".gmt-hint").textContent = hint;

  return card;
}

function renderChampionships(seasonData) {
  championshipsPanel.innerHTML = "";
  seasonData.championships.forEach((championship, index) => {
    championshipsPanel.appendChild(buildChampionshipCard(championship, index));
  });
  const noMatches = document.createElement("p");
  noMatches.className = "no-matches";
  noMatches.hidden = true;
  noMatches.textContent = "No championships match your search.";
  championshipsPanel.appendChild(noMatches);
}

function renderEvents(seasonData) {
  eventsPanel.innerHTML = "";
  if (seasonData.special_events.length === 0) {
    const p = document.createElement("p");
    p.className = "gmt-hint";
    p.textContent = "No special events announced right now.";
    eventsPanel.appendChild(p);
  } else {
    seasonData.special_events.forEach((event, index) => {
      eventsPanel.appendChild(buildEventCard(event, index));
    });
  }
  const noMatches = document.createElement("p");
  noMatches.className = "no-matches";
  noMatches.hidden = true;
  noMatches.textContent = "No special events match your search.";
  eventsPanel.appendChild(noMatches);
}

// ---- Timeslot rows ----

function populateDaySelect(select, sessionTimesByDay) {
  select.innerHTML = "";
  for (const [value, label] of availableDays(sessionTimesByDay)) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = label;
    select.appendChild(option);
  }
}

function populateTimeSelect(select, championship, day) {
  select.innerHTML = "";
  const times = (championship.session_times_by_day || {})[String(day)] || [];
  for (const t of times) {
    const option = document.createElement("option");
    option.value = String(timeIndexForTime(championship, t));
    option.textContent = `${t} GMT`;
    select.appendChild(option);
  }
}

function pyDowToJsDow(pyDow) {
  return (pyDow + 1) % 7;
}

// "Next occurrence of this GMT weekday+time" converted into the picked timezone —
// day_of_week here follows Python's convention (0=Monday..6=Sunday), while JS
// Date's getUTCDay()/Date.UTC() use 0=Sunday, hence the +1 shift.
function gmtSlotToLocalLabel(pyDow, hour, minute, timeZone) {
  const targetJsDow = pyDowToJsDow(pyDow);
  const now = new Date();
  const daysAhead = (targetJsDow - now.getUTCDay() + 7) % 7;
  let candidate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + daysAhead, hour, minute, 0));
  if (daysAhead === 0 && candidate.getTime() <= now.getTime()) {
    candidate = new Date(candidate.getTime() + 7 * 24 * 3600 * 1000);
  }
  try {
    const weekday = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone }).format(candidate);
    const time = new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone }).format(candidate);
    return `${weekday} ${time}`;
  } catch (e) {
    return null;
  }
}

function updateSlotRowLocal(row, seasonData) {
  const daySelect = row.querySelector(".slot-day");
  const timeSelect = row.querySelector(".slot-time");
  const badge = row.querySelector(".local-badge");
  const championshipIndex = Number(row.closest(".slots-container").dataset.championshipIndex);
  const championship = seasonData.championships[championshipIndex];
  const timeGmt = championship.session_time_options[Number(timeSelect.value)];
  if (!timeGmt || timezoneSelect.value === "UTC") {
    badge.textContent = "";
    return;
  }
  const [hh, mm] = timeGmt.split(":").map(Number);
  const label = gmtSlotToLocalLabel(Number(daySelect.value), hh, mm, timezoneSelect.value);
  badge.textContent = label ? `${label} local` : "";
}

function addSlotRow(championshipIndex, seasonData, presetDay, presetTimeIndex) {
  const championship = seasonData.championships[championshipIndex];
  const slotsContainer = document.querySelector(`.slots-container[data-championship-index="${championshipIndex}"]`);
  const row = slotRowTemplate.content.firstElementChild.cloneNode(true);
  const daySelect = row.querySelector(".slot-day");
  const timeSelect = row.querySelector(".slot-time");

  populateDaySelect(daySelect, championship.session_times_by_day);
  if (presetDay !== undefined && [...daySelect.options].some((o) => o.value === String(presetDay))) {
    daySelect.value = String(presetDay);
  }
  populateTimeSelect(timeSelect, championship, Number(daySelect.value));
  if (presetTimeIndex !== undefined && [...timeSelect.options].some((o) => o.value === String(presetTimeIndex))) {
    timeSelect.value = String(presetTimeIndex);
  }

  daySelect.addEventListener("change", () => {
    populateTimeSelect(timeSelect, championship, Number(daySelect.value));
    updateSlotRowLocal(row, seasonData);
    recomputeCode();
  });
  timeSelect.addEventListener("change", () => {
    updateSlotRowLocal(row, seasonData);
    recomputeCode();
  });
  row.querySelector(".remove-slot").addEventListener("click", () => {
    row.remove();
    recomputeCode();
  });

  slotsContainer.appendChild(row);
  updateSlotRowLocal(row, seasonData);
  return row;
}

// ---- Calendar code / result panel ----

function collectSelections() {
  const championships = [];
  document.querySelectorAll(".series-checkbox:checked").forEach((checkbox) => {
    const index = Number(checkbox.dataset.championshipIndex);
    const slotsContainer = document.querySelector(`.slots-container[data-championship-index="${index}"]`);
    const timeslots = [];
    slotsContainer.querySelectorAll(".slot-row").forEach((row) => {
      const day = Number(row.querySelector(".slot-day").value);
      const timeIndex = Number(row.querySelector(".slot-time").value);
      if (Number.isInteger(day) && Number.isInteger(timeIndex) && timeIndex >= 0) {
        timeslots.push({ day, timeIndex });
      }
    });
    championships.push({ index, timeslots });
  });
  const events = [];
  document.querySelectorAll(".event-checkbox:checked").forEach((checkbox) => {
    events.push({ index: Number(checkbox.dataset.eventIndex) });
  });
  return { championships, events };
}

let currentManifest = null;
let currentCode = null;
// A code with no championship/special-event records at all (just the season
// code) carries no real selection — it isn't treated as a valid code for
// query-param/cookie purposes, only as the base to build a real code from.
let currentCodeHasSelections = false;

// Remembers the last copied calendar code so a returning visitor (no ?code=
// in the URL) can be greeted with their existing selections pre-loaded —
// see the CALENDAR_CODE_COOKIE read in main()'s boot sequence below. It's a
// non-essential (functionality) cookie under GDPR/ePrivacy, so it's only ever
// set or read once the visitor has opted in via the consent banner — see
// COOKIE_CONSENT_KEY below.
const CALENDAR_CODE_COOKIE = "ircal_code";
// Same treatment for the timezone picker and the license/category filter
// chips: remembered as non-essential cookies once consent is given, so a
// returning visitor's explicit picks stick instead of resetting to the
// browser-detected timezone / all-filters-on defaults on every visit.
const TIMEZONE_COOKIE = "ircal_timezone";
const LICENSE_FILTER_COOKIE = "ircal_licenses";
const CATEGORY_FILTER_COOKIE = "ircal_categories";
const COOKIE_MAX_AGE_DAYS = 365;

function setCookie(name, value, days) {
  const maxAge = days * 24 * 60 * 60;
  document.cookie = `${name}=${encodeURIComponent(value)}; max-age=${maxAge}; path=/; SameSite=Lax`;
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function clearCookie(name) {
  document.cookie = `${name}=; max-age=0; path=/; SameSite=Lax`;
}

function hasCalendarCodeSelections(code) {
  const parsed = parseCalendarCode(code);
  return parsed.championships.length > 0 || parsed.events.length > 0;
}

// The consent choice itself is stored in localStorage rather than a cookie —
// it's what lets us avoid ever touching CALENDAR_CODE_COOKIE before a decision
// is made, so no non-essential cookie is written pre-consent.
const COOKIE_CONSENT_KEY = "ircal_cookie_consent";

function getCookieConsent() {
  try {
    return window.localStorage.getItem(COOKIE_CONSENT_KEY);
  } catch (e) {
    return null;
  }
}

function setCookieConsent(value) {
  try {
    window.localStorage.setItem(COOKIE_CONSENT_KEY, value);
  } catch (e) {
    /* storage unavailable — banner will just reappear next visit */
  }
}

function hasCookieConsent() {
  return getCookieConsent() === "accepted";
}

function recomputeCode() {
  const { championships, events } = collectSelections();
  const code = encodeCalendarCode({ seasonCode: currentManifest.current, championships, events });
  currentCode = code;
  currentCodeHasSelections = championships.length > 0 || events.length > 0;
  resultUrlEl.textContent = `${WORKER_BASE_URL}/calendar/${code}.ics`;
  copyUrlButton.disabled = !currentCodeHasSelections;
  subscribeUrlBox.classList.toggle("is-disabled", !currentCodeHasSelections);
  if (!currentCodeHasSelections) subscribeUrlBox.open = false;
  const url = new URL(window.location.href);
  if (currentCodeHasSelections) {
    url.searchParams.set("code", code);
  } else {
    url.searchParams.delete("code");
  }
  window.history.replaceState(null, "", url);
}

// ---- Paste-code / manage flow ----

function tickChampionship(index, timeslots, seasonData) {
  const checkbox = document.querySelector(`.series-checkbox[data-championship-index="${index}"]`);
  if (!checkbox) return;
  checkbox.checked = true;
  checkbox.closest(".series-card").classList.add("is-checked");
  const slotsContainer = document.querySelector(`.slots-container[data-championship-index="${index}"]`);
  slotsContainer.innerHTML = "";
  if (timeslots.length === 0) {
    addSlotRow(index, seasonData);
    return;
  }
  for (const slot of timeslots) {
    const timeIndex = timeIndexForTime(seasonData.championships[index], slot.timeGmt);
    addSlotRow(index, seasonData, slot.day, timeIndex >= 0 ? timeIndex : undefined);
  }
}

function tickEvent(index) {
  const checkbox = document.querySelector(`.event-checkbox[data-event-index="${index}"]`);
  if (!checkbox) return;
  checkbox.checked = true;
  checkbox.closest(".series-card").classList.add("is-checked");
}

function extractCode(pasted) {
  const match = pasted.trim().match(/\d{4}S\d[0-9A-Za-z]*/);
  return match ? match[0] : null;
}

function showPasteMessage(text, isError) {
  pasteMessage.textContent = text;
  pasteMessage.hidden = false;
  pasteMessage.classList.toggle("is-error", Boolean(isError));
  pasteMessage.classList.toggle("is-success", !isError);
}

function applyCodeToCurrentSeason(code, seasonData) {
  const decoded = decodeCalendarCode(code, currentManifest);
  if (!decoded.valid) {
    return { ok: false, message: "That doesn't look like a valid calendar code." };
  }
  if (decoded.seasonCode === currentManifest.current) {
    const resolved = resolveSelections(decoded, seasonData);
    for (const c of resolved.championships) tickChampionship(c.index, c.timeslots, seasonData);
    for (const e of resolved.events) tickEvent(e.index);
    recomputeCode();
    return { ok: true, message: "Loaded your existing selections — change anything and copy the new URL." };
  }
  if (decoded.seasonCode === currentManifest.previous) {
    return { ok: false, needsRollover: true, decoded };
  }
  return { ok: false, message: "That code is from too old a season to recover — please re-pick from scratch." };
}

async function handlePastedCode(code, seasonData) {
  const result = applyCodeToCurrentSeason(code, seasonData);
  if (result.ok) {
    showPasteMessage(result.message, false);
    return;
  }
  if (result.needsRollover) {
    await applyRollover({
      decoded: result.decoded,
      manifest: currentManifest,
      currentSeasonData: seasonData,
      tickChampionship: (index, timeslots) => tickChampionship(index, timeslots, seasonData),
      tickEvent,
      recomputeCode,
      showMessage: showPasteMessage,
      bannerContainer: document.getElementById("rollover-banners"),
    });
    return;
  }
  showPasteMessage(result.message, true);
}

// ---- Filter (text + license) ----

function checkedLicenses() {
  const set = new Set();
  document.querySelectorAll(".license-filter-checkbox").forEach((cb) => {
    if (cb.checked) set.add(cb.value);
  });
  return set;
}

function checkedCategories() {
  const set = new Set();
  document.querySelectorAll(".category-filter-checkbox").forEach((cb) => {
    if (cb.checked) set.add(cb.value);
  });
  return set;
}

function applyFilter() {
  const query = filterInput.value.trim().toLowerCase();
  filterClear.hidden = filterInput.value.length === 0;
  const licenses = checkedLicenses();
  const licenseCheckboxes = document.querySelectorAll(".license-filter-checkbox");
  const licenseFilterActive = licenseCheckboxes.length > 0 && Array.from(licenseCheckboxes).some((cb) => !cb.checked);
  const categories = checkedCategories();
  const categoryCheckboxes = document.querySelectorAll(".category-filter-checkbox");
  const categoryFilterActive = categoryCheckboxes.length > 0 && Array.from(categoryCheckboxes).some((cb) => !cb.checked);

  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const cards = panel.querySelectorAll(".series-card");
    let anyVisible = false;
    cards.forEach((card) => {
      const nameEl = card.querySelector(".series-name:not([hidden])");
      const name = nameEl ? nameEl.textContent.toLowerCase() : "";
      const nameMatch = !query || name.includes(query);
      const license = card.dataset.license || "";
      const licenseMatch = !license || licenses.has(license);
      // Cards with no category (including "UNRANKED", normalized away above)
      // always match — the category filter only ever gates real categories.
      const category = card.dataset.category || "";
      const categoryMatch = !category || categories.has(category);
      const match = nameMatch && licenseMatch && categoryMatch;
      card.hidden = !match;
      if (match) anyVisible = true;
    });
    const emptyMsg = panel.querySelector(".no-matches");
    if (emptyMsg) {
      emptyMsg.hidden = (!query && !licenseFilterActive && !categoryFilterActive) || anyVisible || cards.length === 0;
    }
  });
}

// ---- Boot ----

async function main() {
  let manifest, seasonData;
  try {
    ({ manifest, seasonData } = await loadCurrentSeason());
  } catch (err) {
    championshipsPanel.innerHTML = `<p class="loading-hint">Couldn't load the current season's schedule (${err.message}). Try refreshing.</p>`;
    return;
  }
  currentManifest = manifest;

  // Captured now, before anything (recomputeCode included) has a chance to
  // rewrite window.location's own ?code= param — see the boot-sequence note below.
  // A cookie left by a previous "copy calendar URL" only kicks in when the
  // request itself had no ?code=, so an explicit link always wins over it —
  // and only when the visitor has actually opted in (see hasCookieConsent).
  const rawInitialCode = new URLSearchParams(window.location.search).get("code")
    || (hasCookieConsent() ? getCookie(CALENDAR_CODE_COOKIE) : null);
  // A code with no championship/special-event records (just a bare season
  // code) isn't a valid code to load from the URL or cookie — treat it the
  // same as no code being present at all.
  const initialCode = rawInitialCode && hasCalendarCodeSelections(rawInitialCode) ? rawInitialCode : null;

  document.getElementById("season-heading").textContent = formatSeasonLabel(seasonData.season);

  renderChampionships(seasonData);
  renderEvents(seasonData);

  // ---- Tabs ----
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const target = btn.dataset.tab;
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.hidden = panel.dataset.panel !== target;
      });
    });
  });

  // ---- Mobile toolbar toggles: filter / timezone panels collapse behind
  // icon buttons on narrow viewports (see the max-width: 640px CSS block) ----
  filterToggle.addEventListener("click", () => {
    const isOpen = filtersRow.classList.toggle("is-open");
    filterToggle.classList.toggle("is-active", isOpen);
    filterToggle.setAttribute("aria-expanded", String(isOpen));
  });
  tzToggle.addEventListener("click", () => {
    const isOpen = tzPicker.classList.toggle("is-open");
    tzToggle.classList.toggle("is-active", isOpen);
    tzToggle.setAttribute("aria-expanded", String(isOpen));
  });

  // ---- Timezone select ----
  const fallbackTimezones = [
    "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
    "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata", "Australia/Sydney",
  ];
  let timezones = fallbackTimezones;
  if (typeof Intl.supportedValuesOf === "function") {
    try {
      timezones = ["UTC", ...Intl.supportedValuesOf("timeZone").filter((tz) => tz !== "UTC")];
    } catch (e) {
      /* keep fallback */
    }
  }
  for (const tz of timezones) {
    const option = document.createElement("option");
    option.value = tz;
    option.textContent = tz;
    timezoneSelect.appendChild(option);
  }
  try {
    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (detected && timezoneSelect.querySelector(`option[value="${detected}"]`)) {
      timezoneSelect.value = detected;
    }
  } catch (e) {
    /* keep default */
  }
  // A remembered explicit pick overrides the browser's auto-detected
  // timezone — Intl re-detects the same "local" zone on every visit, so
  // without this a saved choice would never stick.
  const savedTimezone = hasCookieConsent() ? getCookie(TIMEZONE_COOKIE) : null;
  if (savedTimezone && timezoneSelect.querySelector(`option[value="${savedTimezone}"]`)) {
    timezoneSelect.value = savedTimezone;
  }
  timezoneSelect.addEventListener("change", () => {
    document.querySelectorAll(".slot-row").forEach((row) => updateSlotRowLocal(row, seasonData));
    if (hasCookieConsent()) setCookie(TIMEZONE_COOKIE, timezoneSelect.value, COOKIE_MAX_AGE_DAYS);
  });

  // ---- License filter chips ----
  // A saved cookie (a comma-joined list of the codes that were left checked)
  // overrides the all-checked default so a returning visitor's narrowed-down
  // filter reappears instead of resetting to "show everything".
  const savedLicenseCookie = hasCookieConsent() ? getCookie(LICENSE_FILTER_COOKIE) : null;
  const savedLicenses = savedLicenseCookie === null ? null : new Set(savedLicenseCookie.split(",").filter(Boolean));
  for (const tier of LICENSE_TIERS) {
    const label = document.createElement("label");
    label.className = "license-toggle";
    label.title = tier.name;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "license-filter-checkbox";
    input.value = tier.code;
    input.checked = savedLicenses ? savedLicenses.has(tier.code) : true;
    const chip = document.createElement("span");
    chip.className = "license-chip";
    chip.textContent = tier.code;
    chip.style.setProperty("--badge-bg", tier.color);
    chip.style.setProperty("--badge-fg", tier.textColor);
    label.append(input, chip);
    licenseFilterContainer.appendChild(label);
  }

  // ---- Category filter chips ----
  const savedCategoryCookie = hasCookieConsent() ? getCookie(CATEGORY_FILTER_COOKIE) : null;
  const savedCategories = savedCategoryCookie === null ? null : new Set(savedCategoryCookie.split(",").filter(Boolean));
  for (const tier of CATEGORY_TIERS) {
    const label = document.createElement("label");
    label.className = "category-toggle";
    label.title = tier.name;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "category-filter-checkbox";
    input.value = tier.code;
    input.checked = savedCategories ? savedCategories.has(tier.code) : true;
    const chip = document.createElement("span");
    chip.className = "category-chip";
    chip.innerHTML = tier.icon;
    label.append(input, chip);
    categoryFilterContainer.appendChild(label);
  }

  filterInput.addEventListener("input", applyFilter);
  filterClear.addEventListener("click", () => {
    filterInput.value = "";
    applyFilter();
    filterInput.focus();
  });
  licenseFilterContainer.addEventListener("change", () => {
    if (hasCookieConsent()) setCookie(LICENSE_FILTER_COOKIE, [...checkedLicenses()].join(","), COOKIE_MAX_AGE_DAYS);
    applyFilter();
  });
  categoryFilterContainer.addEventListener("change", () => {
    if (hasCookieConsent()) setCookie(CATEGORY_FILTER_COOKIE, [...checkedCategories()].join(","), COOKIE_MAX_AGE_DAYS);
    applyFilter();
  });
  applyFilter();

  // ---- Championship card interactions (event delegation) ----
  championshipsPanel.addEventListener("click", (event) => {
    const addButton = event.target.closest(".add-slot");
    if (!addButton) return;
    const index = Number(addButton.dataset.championshipIndex);
    addSlotRow(index, seasonData);
    const checkbox = document.querySelector(`.series-checkbox[data-championship-index="${index}"]`);
    checkbox.checked = true;
    checkbox.closest(".series-card").classList.add("is-checked");
    recomputeCode();
  });
  championshipsPanel.addEventListener("change", (event) => {
    if (!event.target.classList.contains("series-checkbox")) return;
    const checkbox = event.target;
    const card = checkbox.closest(".series-card");
    card.classList.toggle("is-checked", checkbox.checked);
    const index = Number(checkbox.dataset.championshipIndex);
    const slotsContainer = card.querySelector(".slots-container");
    if (checkbox.checked && slotsContainer.children.length === 0) {
      addSlotRow(index, seasonData);
    }
    recomputeCode();
  });
  eventsPanel.addEventListener("change", (event) => {
    if (!event.target.classList.contains("event-checkbox")) return;
    event.target.closest(".series-card").classList.toggle("is-checked", event.target.checked);
    recomputeCode();
  });

  // ---- Copy subscribe URL ----
  copyUrlButton.addEventListener("click", async () => {
    if (currentCode && currentCodeHasSelections && hasCookieConsent()) setCookie(CALENDAR_CODE_COOKIE, currentCode, COOKIE_MAX_AGE_DAYS);
    try {
      await navigator.clipboard.writeText(resultUrlEl.textContent);
      const original = copyUrlButton.textContent;
      copyUrlButton.textContent = "Copied!";
      setTimeout(() => {
        copyUrlButton.textContent = original;
      }, 1500);
    } catch (e) {
      /* clipboard unavailable — user can still select+copy the text manually */
    }
  });

  // A <details> element has no native "disabled" — block the toggle by
  // intercepting the click on its <summary> before it takes effect.
  subscribeUrlBox.querySelector("summary").addEventListener("click", (event) => {
    if (!currentCodeHasSelections) event.preventDefault();
  });

  // ---- Help dialog ----
  const helpDialog = document.getElementById("help-dialog");
  document.getElementById("help-button").addEventListener("click", () => {
    helpDialog.showModal();
  });
  helpDialog.addEventListener("click", (event) => {
    if (event.target === helpDialog) helpDialog.close();
  });

  // ---- Cookie consent banner ----
  // Shown until the visitor picks Accept/Reject; the choice (not the
  // calendar-code cookie itself) is what's remembered, so it isn't asked again.
  //
  // The page's top padding tracks the banner's own measured height rather
  // than a fixed constant, via a ResizeObserver. Content/annoyance blockers
  // commonly hide banners like this one with a cosmetic CSS rule that never
  // touches the `hidden` attribute, which would otherwise leave a
  // fixed-height gap at the top of the page with nothing in it.
  const syncConsentBannerSpace = () => {
    const height = cookieConsentBanner.hidden ? 0 : cookieConsentBanner.offsetHeight;
    document.documentElement.style.setProperty("--consent-banner-space", `${height}px`);
  };
  new ResizeObserver(syncConsentBannerSpace).observe(cookieConsentBanner);

  cookieConsentBanner.hidden = getCookieConsent() !== null;
  syncConsentBannerSpace();
  document.getElementById("cookie-consent-accept").addEventListener("click", () => {
    setCookieConsent("accepted");
    // Capture whatever's already picked at the moment of consent, rather
    // than waiting for the next change event, so a visitor who set their
    // timezone/filters before accepting doesn't lose that pick.
    setCookie(TIMEZONE_COOKIE, timezoneSelect.value, COOKIE_MAX_AGE_DAYS);
    setCookie(LICENSE_FILTER_COOKIE, [...checkedLicenses()].join(","), COOKIE_MAX_AGE_DAYS);
    setCookie(CATEGORY_FILTER_COOKIE, [...checkedCategories()].join(","), COOKIE_MAX_AGE_DAYS);
    cookieConsentBanner.hidden = true;
    syncConsentBannerSpace();
  });
  document.getElementById("cookie-consent-reject").addEventListener("click", () => {
    setCookieConsent("rejected");
    clearCookie(CALENDAR_CODE_COOKIE);
    clearCookie(TIMEZONE_COOKIE);
    clearCookie(LICENSE_FILTER_COOKIE);
    clearCookie(CATEGORY_FILTER_COOKIE);
    cookieConsentBanner.hidden = true;
    syncConsentBannerSpace();
  });

  // ---- Paste-code box ----
  // The "toggle" event fires for every open/close, whatever triggered it
  // (this button, the native <summary> on wide viewports, or the deep-link
  // boot sequence below setting pasteBox.open directly) — so listening to it
  // here is the one place that keeps the button's state honest.
  calendarCodeToggle.addEventListener("click", () => {
    pasteBox.open = !pasteBox.open;
    if (pasteBox.open) pasteBox.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  pasteBox.addEventListener("toggle", () => {
    calendarCodeToggle.classList.toggle("is-active", pasteBox.open);
    calendarCodeToggle.setAttribute("aria-expanded", String(pasteBox.open));
  });
  document.getElementById("paste-code-apply").addEventListener("click", () => {
    const code = extractCode(pasteInput.value);
    if (!code) {
      showPasteMessage("Couldn't find a calendar code in that.", true);
      return;
    }
    handlePastedCode(code, seasonData);
  });

  // ---- AdSense (optional, only when configured) ----
  if (ADS_PUBLISHER_ID) {
    const adSlot = document.getElementById("ad-slot");
    adSlot.hidden = false;
    const script = document.createElement("script");
    script.async = true;
    script.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(ADS_PUBLISHER_ID)}`;
    script.crossOrigin = "anonymous";
    document.head.appendChild(script);
    const ins = document.createElement("ins");
    ins.className = "adsbygoogle";
    ins.style.display = "block";
    ins.dataset.adClient = ADS_PUBLISHER_ID;
    ins.dataset.adFormat = "auto";
    ins.dataset.fullWidthResponsive = "true";
    adSlot.appendChild(ins);
    (window.adsbygoogle = window.adsbygoogle || []).push({});

    // Google sets data-ad-status once it's done trying to fill the slot.
    // Confirmed experimentally: even when unfilled, it still inserts a
    // same-size measurement iframe into aswift_1_host, so an "is the host
    // empty" check never fires — data-ad-status is the only reliable signal
    // that nothing was actually served, and we hide the bar in that case
    // instead of showing a blank strip pinned to the viewport.
    const adStatusObserver = new MutationObserver(() => {
      adStatusObserver.disconnect();
      if (ins.dataset.adStatus === "unfilled") {
        adSlot.hidden = true;
      }
    });
    adStatusObserver.observe(ins, { attributes: true, attributeFilter: ["data-ad-status"] });
  }

  // ---- Deep link: ?code=... (or a remembered cookie) reopens the picker pre-filled ----
  // initialCode was captured at the top of main(), before recomputeCode() ever
  // ran — recomputeCode() itself rewrites the URL's ?code= param on every change,
  // so reading window.location here instead would see its own write and wrongly
  // treat every fresh visit as if it arrived with a real deep link.
  if (initialCode) {
    pasteBox.open = true;
    pasteInput.value = initialCode;
    await handlePastedCode(initialCode, seasonData);
  } else {
    recomputeCode();
  }
}

main();
