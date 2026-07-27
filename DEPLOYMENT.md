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
A custom domain can be attached later from the Cloudflare dashboard (Workers &
Pages → your worker → Settings → Triggers → Custom Domains) if you want a
nicer URL than `*.workers.dev`.

## 3. Set repo Variables

Settings → Secrets and variables → Actions → **Variables** (not Secrets — these
aren't sensitive; they end up in public page source once deployed either way):

- `WORKER_BASE_URL` (**required**) — the Worker's public base URL, e.g.
  `https://iracing-calendar-worker.your-subdomain.workers.dev`. Baked into
  `docs/config.js` at Pages-deploy time so the site knows where to build
  subscribe URLs. Without it, the site falls back to `http://localhost:8787`
  (fine for local dev, wrong for production).
- `ADS_PUBLISHER_ID` (**optional**) — a Google AdSense publisher id
  (`ca-pub-...`). Leave unset to render the site with no ad slot at all.

Also update `worker/wrangler.toml`'s `PAGES_BASE_URL` to your actual Pages URL
(e.g. `https://<you>.github.io/<repo-name>`) — the Worker fetches
`data/manifest.json` and `data/{season}.json` live from there on every
request (see ARCHITECTURE.md), so it needs to know where "there" is.

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
  re-check the repo Secrets from step 2.
- **`build-cache.yml` fails to push**: the default `GITHUB_TOKEN` needs
  `contents: write` (already set in the workflow) — if branch protection on
  `main` blocks Actions from pushing directly, either relax that for this
  bot or switch the workflow to push via a PAT with the appropriate scope.
