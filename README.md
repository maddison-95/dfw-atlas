# DFW Neighborhood Atlas — site bundle (v10: review fixes)

This folder is the whole website. Nothing runs on a server: one HTML page, a few data files, and the scripts
that refresh the data (mostly run automatically by GitHub Actions).

```
index.html               the interactive map, generated from template3.html + data/areas_meta.json — don't
                          hand-edit; edit those two and run assemble3.py (or ask Claude to)
template3.html           the page's actual source code (HTML/CSS/JS in one file)
assemble3.py             builds index.html from template3.html + data/areas_meta.json
data/geo.json            neighborhood + zip shapes, in real map coordinates (generated, don't edit)
data/areas_meta.json     the neighborhood list with all its panel content (generated, don't edit)
data/atlas.json          a slimmer neighborhood list the refresh scripts read — generated, don't edit
data/housing.json        home values, rents, sales, Census figures — written by scripts/refresh_housing.py
data/schools.json        schools with level, type, location, TEA rating — written by scripts/refresh_schools.py
data/lot_stats.json      median lot size/value/home age per neighborhood — written by scripts/refresh_lots.py
data-sources/            the two files you download by hand (TEA ratings, NCES private schools)
scripts/                 the refresh scripts
.github/workflows/       the automated refresh jobs (GitHub Actions)
internal-lending/        a SEPARATE, registration-gated (name/email/mobile + captcha), non-public page for
                          lending-market signals — not part of the public map, not linked from it. Read
                          internal-lending/README.md before you share that link with anyone, and before you
                          set up the free Cloudflare Worker its gate depends on.
internal-lending/worker/register.js   the Cloudflare Worker source that checks the captcha and emails you
                          each registration — deploy it per internal-lending/README.md's setup steps.
robots.txt               tells search engines to skip internal-lending/
```

## What's new in v10 (code review pass)
* **Lender disclosures now show on phones.** The Equal Housing Lender / NMLS footer was hidden below 900px
  wide; it's now a compact strip over the map plus a tighter panel footer. (Compliance item — was a real gap.)
* **Street level is readable again after clicking a neighborhood.** The selected neighborhood's color used to
  stay at 62% opacity at every zoom, so it sat as a heavy wash over the very streets and lot lines the click
  had just zoomed to; it now fades with zoom like everything else, and lot lines draw above the tint.
* **Zip highlight resets.** Selecting a zip, then a neighborhood, then switching back to the zip layer left the
  old zip lit yellow forever. Fixed.
* **Zip search works across areas.** Typing a zip from another area now offers that zip and jumps to its area,
  the same way neighborhood search already did.
* **"Source: zip X" label** on the Homes/People tabs could name the wrong zip when only a neighborhood's second
  zip had data. Fixed.
* **"Custom-build hotspots" toggle** now also keeps master-planned communities (production + custom builders,
  e.g. Canyon Falls) lit instead of dimming them with "established".
* **Monthly refresh is resilient.** One data source being down (the state parcel server, Zillow, the CFPB API)
  used to fail the whole run and commit nothing; each step now runs independently, whatever succeeded gets
  committed, and the run is still flagged red (with the failure email) if anything failed.
* Smaller: hover tooltip flips below the cursor near the top edge instead of clipping; `#neighborhood` links
  work after load too (not only on first paint); the schools layer no longer polls forever if `geo.json`
  fails; area buttons scroll horizontally on phones now that there are eight; internal-lending page defines
  its captcha callbacks before the widget script loads, re-locks the button after a failed captcha (the token
  is single-use), and its empty-state message names the right GitHub Action.
* Two of the v9 corridor shapes (Canyon Falls/Bartonville, Hidden Creek/Silver Lake) overlapped by 33–50%
  because of centroid errors; re-placed.

## What's new in v9
* **Lakes are unmistakably blue now.** The basemap actually renders water as a muted gray, and translucent
  neighborhood colors sat on top of it, so a lake could look the same as the land around it. Real lakes and
  rivers are now redrawn a second time, in a solid blue, above every neighborhood/zip color fill.
* **Region colors that were themselves blue-ish are gone**, so a neighborhood swatch never reads as water —
  this affected Far North Dallas, East Plano & Murphy, Celina, McKinney, Westlake/Trophy Club/Roanoke and
  Rockwall (several of which sit right on real lakes), plus a stronger blue used for Oak Cliff.
* **Southlake now has its own top button**, split out from the old combined "Southlake, Westlake &
  Colleyville" button. Westlake and Colleyville now share their own "Westlake & Colleyville" button.
* **New area: "Irving, Coppell & Grapevine"** — 17 new neighborhoods filling in the higher-end / active
  custom-construction corridor between Dallas and Southlake: Las Colinas, Hackberry Creek and Cottonwood
  Valley (Irving); Old Town Coppell, Riverchase and Panther Creek (Coppell); Historic Downtown Grapevine,
  Hidden Creek and Silver Lake Estates (Grapevine); and the Flower Mound / Argyle / Bartonville / Double Oak /
  Copper Canyon / Northlake-Justin belt along I-35W, one of DFW's most active large-lot custom-home corridors.
  Same honest caveat as the rest of the hand-curated set: tier, build activity and district are a starting
  classification from general knowledge, not survey data — the page says so, and it's worth spot-checking a
  few before relying on them with Realtor partners. Home values / rents / demographics will fill in on their
  own from the next monthly refresh once these zips are in `data/atlas.json`'s lookup (they already are).

## What's new in v8
* **The "Get pre-approved" tab is gone from the public map.** Every neighborhood panel used to end with a
  lead-capture form (name/email/phone/timeline) that only produced a copy-paste note, since it was never wired
  to a CRM. That tab, its button, and its now-unused CSS have all been removed — panels are Overview / Schools /
  People / Homes only. Contact info can come back later once there's a real destination (a form that actually
  submits somewhere), just say the word.
* **The internal-lending page now registers visitors instead of using a shared passphrase.** Anyone opening
  `internal-lending/` gives their name, email and mobile number and solves a live captcha (Cloudflare
  Turnstile, free) before seeing anything; you get an email the moment someone gets in. There's still just one
  permission level — registered or not — no different access tiers. This needs a small one-time setup (a free
  Cloudflare Worker + a free Resend account) since GitHub Pages can't check a captcha or send an email on its
  own; full walkthrough in `internal-lending/README.md`. **Until that setup is done, the page's registration
  form won't work** (it says so on the page itself). This is meaningfully stronger than a shared password, but
  — like the old passphrase — it's still enforced by the page's own JavaScript, not a real server sitting in
  front of GitHub Pages, so it doesn't stop someone who already has the exact link. Cloudflare Access remains
  the upgrade path if that ever needs to be airtight.

## What's new in v6
* **Full coverage of all 5 counties.** Every incorporated city and named community in Dallas, Collin, Denton,
  Tarrant and Rockwall counties is now on the map, not just the six hand-picked areas from before — a few
  hundred additional shapes, added automatically from Census's own city boundaries (not hand-drawn), with zip
  code(s) and school district worked out by overlaying those boundaries on Census's zip and school-district
  maps. They show up in whichever of the 6 areas they're geographically closest to, under a new "More nearby
  cities" legend chip, so the existing hand-curated neighborhoods (Lakewood, Southlake, etc.) aren't disturbed.
  **Honest caveat:** nobody's reviewed each of these 150+ new places individually, so unlike the original set
  they start with no price tier, no build-activity tag, and a generic one-line description instead of a
  written blurb — the panel says so plainly. Price tier fills in on its own once the monthly refresh has zip
  data for it; district is a best-match spatial estimate (labeled as such — always confirm by address, same as
  everywhere else on this map). Run **Actions → "Add full county coverage" → Run workflow** to build this the
  first time; see the new section below. It's a one-time (or occasional) job, not part of the monthly refresh
  — a full 5-county build downloads and processes ~150-250MB of Census map data, which would be wasteful to
  repeat every month for boundaries that rarely change.

## What's new in v5
* **Hovering a neighborhood now shows numbers**, not just its name: zip code(s), a median home price, a median
  lot ("land only") value, a median lot size in acres, and a median home age — all in the small dark tooltip
  that follows your cursor. The same lot figures also now appear as their own row of stats on the Homes tab.
  These come from a new monthly-refreshed file, `data/lot_stats.json` (see below) — they're a neighborhood-wide
  median, distinct from the exact size shown when you click one specific lot (that one is still fetched live,
  described under v4 below).
* **Clicking a neighborhood (or a zip) now zooms in properly.** Before, the map always zoomed just far enough
  to fit the whole neighborhood's outline on screen — for a small neighborhood that was plenty, but for a large
  one it could stop well short of street level. It now flies to at least street/lot level (zoom ~15.5) every
  time, even for the biggest neighborhoods, so you don't have to zoom in by hand afterward.

## What's new in v4
* **Lot lines & sizes.** Turn on the "Lot lines & sizes" chip (top of the map) and zoom in past street level
  (about zoom 15, roughly "you can see individual blocks") — real property-lot outlines appear, pulled live
  from the Texas Geographic Information Office's statewide parcel feed (built from county appraisal district
  data — this one feed covers Dallas, Collin, Denton, Tarrant and Rockwall counties, so no per-county
  integration was needed). Click a lot for its size in both acres and approximate square-foot dimensions
  (e.g. "60 × 150 ft"), plus year built, land/improvement value, market value and legal description where the
  county provides them. Dimensions are estimated from the parcel's outline (a best-fit rectangle), so they're
  labeled as approximate and the popup tells people to confirm with a survey for anything binding. Lots only
  load for the area currently on screen (and only above the zoom threshold), so it never tries to fetch all of
  Dallas County at once. If the county's server is briefly unavailable a small "Lot data unavailable right
  now" note appears near the bottom of the map instead of an error.

## What's new in v3
* The map now sits on a real street map (OpenStreetMap data served free by OpenFreeMap, no key, no account).
  Zoom in and streets, then addresses, appear on their own, like any map site. The stylized neighborhood shapes
  float on top and fade as you zoom in so the streets stay readable.
* Clicking an area button or a region chip flies the map to that area.
* Schools show as dots on the map (blue public, gold magnet, green charter, purple private); zoom in past ~13 for
  names, click a dot for its rating. The Schools tab's level/type filters also filter the dots; clicking a
  school in the list flies to it. "Schools on map" toggles the layer.
* The page loads the MapLibre library from the jsDelivr CDN and the basemap from tiles.openfreemap.org. If your
  website blocks either domain, tell me and I'll switch to a self-hosted copy.

## 1. Put it on the web (free, ~20 minutes, no coding)

1. Create a free account at github.com. Click **New repository**, name it `dfw-atlas`, leave it Public, click Create.
2. Copy this folder's contents into the repository (GitHub Desktop: commit, then Push origin).
3. In the repo go to **Settings → Pages**. Under "Build and deployment" choose Source: *Deploy from a branch*,
   Branch: *main*, folder */ (root)*. Save. After a minute the page shows your address:
   `https://YOURNAME.github.io/dfw-atlas/`. That link is the live map.
4. To put it on your own site, add one line where the map should appear (WordPress: a "Custom HTML" block):
   ```html
   <iframe src="https://maps.michaeladdison.ai/" style="width:100%;height:90vh;border:0;border-radius:16px" title="DFW Neighborhood Atlas" loading="lazy"></iframe>
   ```
   A neighborhood can be linked directly: `.../#lakewood`, `#whitley-place`, `#vaquero`.
5. Custom domain: Settings → Pages → Custom domain, then add the CNAME at your registrar (done: maps.michaeladdison.ai).

## 2. Monthly housing + schools + lot-stats refresh (already on)

1. Free Census API key: https://api.census.gov/data/key_signup.html → repo **Settings → Secrets and variables →
   Actions → New repository secret** named `CENSUS_KEY`. (Done.)
2. **Actions** tab → "Refresh housing + schools + lot data" → **Run workflow**. It downloads Zillow's ZHVI/ZORI
   files and the Census figures, pulls public-school locations from the NCES open-data feed, merges the TEA
   ratings and private schools from `data-sources/`, queries the state's parcel feed for each neighborhood's
   median lot size/value/home age, writes `data/housing.json`, `data/schools.json` and `data/lot_stats.json`,
   and commits them. It repeats on the 3rd of every month. (The lot-stats step alone takes the longest — it's
   one query per neighborhood, roughly 15-20 minutes for the current neighborhood count; that's normal.)
3. Optional sales data (median sale price, days on market): download `zip_code_market_tracker.tsv000.gz` from
   https://www.redfin.com/news/data-center/ and run locally:
   `python scripts/refresh_housing.py --census-key KEY --redfin zip_code_market_tracker.tsv000.gz`, then commit
   the new `data/housing.json`. (The file is ~1 GB, so it is not automated.)

## 3. Full 5-county coverage (one-time, or whenever you want to redo it)

**Actions** tab → **"Add full county coverage"** → **Run workflow**. It downloads Census's own city, zip-code
and school-district boundary files, adds every incorporated place in Dallas, Collin, Denton, Tarrant and
Rockwall counties that isn't already on the map (skipping anything whose name already exists, so your
hand-tuned Dallas/Richardson/Plano/Frisco/McKinney/Southlake/Rockwall neighborhoods are never touched or
duplicated), works out each new place's zip code(s) and school district by overlaying it on Census's own zip
and district maps, and rebuilds `index.html` so the new places show up in the side panel and search, not just
as shapes on the map. Takes 10-20 minutes (it's processing a few hundred megabytes of map data). Safe to
re-run later — it only adds places that aren't already there.

## 4. Yearly: replace the two hand-downloaded school files

* `data-sources/tea_ratings.csv` — TEA → Accountability → Data Downloads → Report level **Campus**, category
  Accountability Summary, element "Accountability Rating & Overall Score", CSV. New ratings come out each August.
* `data-sources/pss.csv` — nces.ed.gov/surveys/pss/pssdata.asp → newest year → Text (CSV) zip → unzip.
  Published every two years.

Drop the new files in with the same names, commit, and run the workflow. If a column name changes, edit the
`COLS` table at the top of `scripts/refresh_schools.py`.

## 5. Editing the map itself

The ~236 hand-curated Dallas-area neighborhoods (names, zips, tiers, build tags, region colors, blurbs) live in
a separate generator project, not in this folder — send edits and a new `data/geo.json` +
`data/areas_meta.json` are generated here, then `assemble3.py` rebuilds `index.html`. The 150+ auto-added
places from full county coverage (section 3) can be hand-edited directly in `data/areas_meta.json` (to add a
blurb, price tier or build tag once you know it) — just also update the matching entry in `data/geo.json`'s
`hoods` list (same `name`), then run `python assemble3.py` and commit.

## Attribution shown on the page
Basemap © OpenStreetMap contributors, served by OpenFreeMap. Zip areas: U.S. Census ZCTA. Home values and rents:
Zillow Research. Sales: Redfin Data Center. Demographics: U.S. Census ACS. School locations: NCES. School ratings:
Texas Education Agency. Private schools: NCES Private School Survey. Parcel lines and lot sizes: county
appraisal districts via the Texas Geographic Information Office (StratMap). Neighborhood areas are stylized.

Lot data note: parcels are used two ways. The exact size shown when you click one specific lot is fetched
live from the state's server each time someone zooms into a new area — never stored in this repo. The median
lot size/value/home age shown on hover and on the Homes tab (`data/lot_stats.json`) is a monthly snapshot, like
the housing and school data. If TxGIO changes that service's address, tell me and I'll point the map at the
new one (one line to change: `PARCELS_URL`, used in both `template3.html` and `scripts/refresh_lots.py`).
