# Deployment: GitHub Pages + a Cloudflare Worker

There's no server to run and no database to back up. Two things need to be
deployed once each, then GitHub Actions keeps both up to date automatically:

1. **GitHub Pages** — the static picker/manager site (`docs/`).
2. **A Cloudflare Worker** — the live `.ics` endpoint (`worker/`), the one
   piece of always-on compute in the system, since GitHub Pages can't execute
   code per request. See [ARCHITECTURE.md](ARCHITECTURE.md) for why.

## 1. Enable GitHub Pages

Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
That's it — `deploy-pages.yml` handles the rest on every push to `docs/**`.

## 2. Create the Cloudflare Worker

1. Sign up for Cloudflare (the free tier is enough — Workers' free plan is
   generous, and this Worker does one small fetch+text-transform per request).
2. Get an **API token**: Cloudflare dashboard → My Profile → API Tokens →
   Create Token → "Edit Cloudflare Workers" template is sufficient.
3. Get your **Account ID**: shown on the right sidebar of any zone's overview
   page, or on the Workers & Pages dashboard.
4. Add both as **repo Secrets** (Settings → Secrets and variables → Actions →
   Secrets):
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID`

`deploy-worker.yml` will deploy `worker/` to
`iracing-calendar-worker.<your-subdomain>.workers.dev` the first time it runs
(triggered by any push to `worker/**` or `shared/**`, or manually — see below).

`worker/wrangler.toml` also declares a Workers **Custom Domain**
(`ics.iracing.pmsoftwaredevs.com`), attached automatically on every
`wrangler deploy` — no manual dashboard step, as long as that hostname's zone
already exists and is active in Cloudflare (see "Custom domain setup" below).
Until that zone is active, the deploy step that attaches the Custom Domain
will fail; re-run `deploy-worker.yml` manually once DNS has propagated.

## 3. Set repo Variables

Settings → Secrets and variables → Actions → **Variables** (not Secrets — these
aren't sensitive; they end up in public page source once deployed either way):

- `WORKER_BASE_URL` (**required**) — the Worker's public base URL, e.g.
  `https://ics.iracing.pmsoftwaredevs.com`. Baked into `docs/config.js` at
  Pages-deploy time so the site knows where to build subscribe URLs. Without
  it, the site falls back to `http://localhost:8787` (fine for local dev,
  wrong for production).
- `ADS_PUBLISHER_ID` (**optional**) — a Google AdSense publisher id
  (`ca-pub-...`). Leave unset to render the site with no ad slot at all.

`worker/wrangler.toml`'s `PAGES_BASE_URL` already points at
`https://iracing.pmsoftwaredevs.com` — the Worker fetches `data/manifest.json`
and `data/{season}.json` live from there on every request (see
ARCHITECTURE.md), so it needs to know where "there" is. Change it only if the
site's custom domain ever changes.

## Custom domain setup

The site and the Worker live on two different hostnames under
`pmsoftwaredevs.com`, deliberately kept independent — the site is plain
GitHub Pages with no Cloudflare involvement, and the Worker's hostname is
fully owned by Cloudflare with no origin fallback (see `worker/wrangler.toml`
for why). This means the two are set up completely separately:

**`iracing.pmsoftwaredevs.com` (the site):**

1. At your DNS provider, add a `CNAME` record:
   `iracing.pmsoftwaredevs.com` → `pmsoftwaredevs.github.io.`
2. Repo Settings → Pages → **Custom domain** → enter
   `iracing.pmsoftwaredevs.com` → Save. GitHub checks the DNS and, once it
   resolves, provisions a Let's Encrypt certificate automatically (can take a
   few minutes to a few hours).
3. Once the certificate is issued, tick **Enforce HTTPS**.

**`ics.iracing.pmsoftwaredevs.com` (the Worker):**

1. In Cloudflare, add a new zone named exactly `ics.iracing.pmsoftwaredevs.com`
   (Add a Site → enter that full hostname, not just `pmsoftwaredevs.com`).
   This only delegates that one subdomain — it does not touch any other DNS
   on `pmsoftwaredevs.com` (mail included).
2. Cloudflare assigns two nameservers for that zone. At your DNS provider, add
   an `NS` record delegating `ics.iracing.pmsoftwaredevs.com` to those two
   nameservers.
3. Wait for the zone to show **Active** in the Cloudflare dashboard (DNS
   propagation, usually well under an hour).
4. Run (or re-run) `deploy-worker.yml` — `worker/wrangler.toml`'s `routes`
   entry attaches the Custom Domain automatically on deploy. No manual step
   in the Cloudflare dashboard needed once the zone is active.

## 4. First-run bootstrap order

`docs/data/` starts empty in a fresh checkout (it's generated, not
hand-written). Bootstrap order matters once, on a brand-new deployment:

1. Run **`build-cache.yml`** once manually (Actions tab → "Refresh season
   cache" → Run workflow) so `docs/data/manifest.json` and the current
   season's JSON actually exist and get committed.
2. That commit's push to `docs/**` triggers **`deploy-pages.yml`**
   automatically — confirm it succeeds and the site loads real championships.
3. Push (or manually trigger) **`deploy-worker.yml`** to get the `.ics`
   endpoint live.
4. Visit the deployed site, pick something, copy the subscribe URL, and
   confirm it returns a real `.ics` (e.g. `curl <subscribe-url>`).

After that, `build-cache.yml`'s daily cron keeps the data fresh and
automatically triggers a Pages republish on every change — no manual steps
needed going forward.

## 5. Manually re-triggering a workflow

Any of the three workflows can be re-run on demand from the **Actions** tab →
select the workflow → **Run workflow**. Useful for: picking up a schedule
correction iRacing published mid-season without waiting for the next cron
tick, forcing a Pages republish without a data change, or redeploying the
Worker after rotating its API token.

## Troubleshooting

- **Pages site loads but shows no championships**: `docs/data/manifest.json`
  is probably missing or stale — run `build-cache.yml` manually (step 4.1
  above), confirm it committed something, then check `deploy-pages.yml` ran
  after that commit.
- **Subscribe URL returns an empty calendar unexpectedly**: check the code's
  season prefix against the live `data/manifest.json` — a code from a season
  outside the current+previous retention window decodes to empty by design
  (ARCHITECTURE.md), not a bug.
- **`deploy-worker.yml` fails at the `wrangler deploy` step**: usually an
  expired/missing `CLOUDFLARE_API_TOKEN` or wrong `CLOUDFLARE_ACCOUNT_ID` —
  re-check the repo Secrets from step 2. If the error specifically mentions
  the Custom Domain / route, the `ics.iracing.pmsoftwaredevs.com` zone likely
  isn't **Active** yet (see "Custom domain setup") — re-run the workflow once
  it is.
- **`build-cache.yml` fails to push**: the default `GITHUB_TOKEN` needs
  `contents: write` (already set in the workflow) — if branch protection on
  `main` blocks Actions from pushing directly, either relax that for this
  bot or switch the workflow to push via a PAT with the appropriate scope.
