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

import { encodeCalendarCode, decodeCalendarCode } from "./shared/calendar-code.js";
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

const nowIso = new Date().toISOString().slice(0, 10);

const championshipsPanel = document.getElementById("championships-panel");
const eventsPanel = document.getElementById("events-panel");
const seriesCardTemplate = document.getElementById("series-card-template");
const slotRowTemplate = document.getElementById("slot-row-template");
const eventCardTemplate = document.getElementById("event-card-template");
const resultCodeEl = document.getElementById("result-code");
const resultUrlEl = document.getElementById("result-url");
const filterInput = document.getElementById("filter-input");
const filterClear = document.getElementById("filter-clear");
const timezoneSelect = document.getElementById("timezone-select");
const licenseFilterContainer = document.getElementById("license-filter");
const pasteBox = document.getElementById("paste-code-box");
const pasteInput = document.getElementById("paste-code-input");
const pasteMessage = document.getElementById("paste-code-message");

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

function recomputeCode() {
  const { championships, events } = collectSelections();
  const code = encodeCalendarCode({ seasonCode: currentManifest.current, championships, events });
  resultCodeEl.textContent = code;
  resultUrlEl.textContent = `${WORKER_BASE_URL}/calendar/${code}.ics`;
  const url = new URL(window.location.href);
  url.searchParams.set("code", code);
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

function applyFilter() {
  const query = filterInput.value.trim().toLowerCase();
  filterClear.hidden = filterInput.value.length === 0;
  const licenses = checkedLicenses();
  const checkboxes = document.querySelectorAll(".license-filter-checkbox");
  const licenseFilterActive = checkboxes.length > 0 && Array.from(checkboxes).some((cb) => !cb.checked);

  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const cards = panel.querySelectorAll(".series-card");
    let anyVisible = false;
    cards.forEach((card) => {
      const nameEl = card.querySelector(".series-name:not([hidden])");
      const name = nameEl ? nameEl.textContent.toLowerCase() : "";
      const nameMatch = !query || name.includes(query);
      const license = card.dataset.license || "";
      const licenseMatch = !license || licenses.has(license);
      const match = nameMatch && licenseMatch;
      card.hidden = !match;
      if (match) anyVisible = true;
    });
    const emptyMsg = panel.querySelector(".no-matches");
    if (emptyMsg) {
      emptyMsg.hidden = (!query && !licenseFilterActive) || anyVisible || cards.length === 0;
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
  const initialCode = new URLSearchParams(window.location.search).get("code");

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
  timezoneSelect.addEventListener("change", () => {
    document.querySelectorAll(".slot-row").forEach((row) => updateSlotRowLocal(row, seasonData));
  });

  // ---- License filter chips ----
  for (const tier of LICENSE_TIERS) {
    const label = document.createElement("label");
    label.className = "license-toggle";
    label.title = tier.name;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "license-filter-checkbox";
    input.value = tier.code;
    input.checked = true;
    const chip = document.createElement("span");
    chip.className = "license-chip";
    chip.textContent = tier.code;
    chip.style.setProperty("--badge-bg", tier.color);
    chip.style.setProperty("--badge-fg", tier.textColor);
    label.append(input, chip);
    licenseFilterContainer.appendChild(label);
  }
  filterInput.addEventListener("input", applyFilter);
  filterClear.addEventListener("click", () => {
    filterInput.value = "";
    applyFilter();
    filterInput.focus();
  });
  licenseFilterContainer.addEventListener("change", applyFilter);
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
  document.getElementById("copy-url-button").addEventListener("click", async () => {
    const button = document.getElementById("copy-url-button");
    try {
      await navigator.clipboard.writeText(resultUrlEl.textContent);
      const original = button.textContent;
      button.textContent = "Copied!";
      setTimeout(() => {
        button.textContent = original;
      }, 1500);
    } catch (e) {
      /* clipboard unavailable — user can still select+copy the text manually */
    }
  });

  // ---- Paste-code box ----
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
  }

  // ---- Deep link: ?code=... reopens the picker pre-filled ----
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
