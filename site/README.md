# DFW Neighborhood Atlas — site bundle (v3: real street basemap)

This folder is the whole website. Nothing runs on a server: one HTML page, a few data files, and two scripts that
refresh the data (run automatically once a month by GitHub Actions).

```
index.html               the interactive map (all six areas) on a real OpenStreetMap basemap
data/geo.json            the stylized neighborhood + zip shapes (generated, don't edit)
data/atlas.json          the neighborhood list the scripts read (zips, names) — generated, don't edit
data/housing.json        home values, rents, sales, Census figures — written by scripts/refresh_housing.py
data/schools.json        schools with level, type, location, TEA rating — written by scripts/refresh_schools.py
data-sources/            the two files you download by hand (TEA ratings, NCES private schools)
scripts/                 the refresh scripts
.github/workflows/       the monthly auto-refresh (GitHub Actions)
```

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

## 2. Monthly housing + schools refresh (already on)

1. Free Census API key: https://api.census.gov/data/key_signup.html → repo **Settings → Secrets and variables →
   Actions → New repository secret** named `CENSUS_KEY`. (Done.)
2. **Actions** tab → "Refresh housing + schools data" → **Run workflow**. It downloads Zillow's ZHVI/ZORI files
   and the Census figures, pulls public-school locations from the NCES open-data feed, merges the TEA ratings and
   private schools from `data-sources/`, writes `data/housing.json` and `data/schools.json`, and commits them.
   It repeats on the 3rd of every month.
3. Optional sales data (median sale price, days on market): download `zip_code_market_tracker.tsv000.gz` from
   https://www.redfin.com/news/data-center/ and run locally:
   `python scripts/refresh_housing.py --census-key KEY --redfin zip_code_market_tracker.tsv000.gz`, then commit
   the new `data/housing.json`. (The file is ~1 GB, so it is not automated.)

## 3. Yearly: replace the two hand-downloaded school files

* `data-sources/tea_ratings.csv` — TEA → Accountability → Data Downloads → Report level **Campus**, category
  Accountability Summary, element "Accountability Rating & Overall Score", CSV. New ratings come out each August.
* `data-sources/pss.csv` — nces.ed.gov/surveys/pss/pssdata.asp → newest year → Text (CSV) zip → unzip.
  Published every two years.

Drop the new files in with the same names, commit, and run the workflow. If a column name changes, edit the
`COLS` table at the top of `scripts/refresh_schools.py`.

## 4. Editing the map itself

Neighborhood names, zips, tiers, build tags and region colors live in the generator project (areas.py,
neighborhoods_seed.py). Send edits and a new index.html + data/geo.json are generated; the other data files don't
change.

## Attribution shown on the page
Basemap © OpenStreetMap contributors, served by OpenFreeMap. Zip areas: U.S. Census ZCTA. Home values and rents:
Zillow Research. Sales: Redfin Data Center. Demographics: U.S. Census ACS. School locations: NCES. School ratings:
Texas Education Agency. Private schools: NCES Private School Survey. Neighborhood areas are stylized.
