# DFW Neighborhood Atlas — site bundle (v6: full 5-county coverage)

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
```

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
