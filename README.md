# DFW Neighborhood Atlas — site bundle

This folder is the whole website. Nothing runs on a server: it is one HTML page, two data files, and a script that
refreshes the data once a month.

```
index.html               the interactive map (all six areas)
data/atlas.json          the neighborhood list the scripts read (zips, names) — generated, don't edit
data/housing.json        home values, rents, sales, Census figures — written by scripts/refresh_housing.py
data/schools.json        schools with level, type, TEA rating — written by scripts/refresh_schools.py
scripts/                 the refresh scripts
.github/workflows/       the monthly auto-refresh (GitHub Actions)
```

## 1. Put it on the web (free, ~20 minutes, no coding)

1. Create a free account at github.com. Click **New repository**, name it `dfw-atlas`, leave it Public, click Create.
2. On the empty repo page click **uploading an existing file**, drag this whole folder's contents in (index.html,
   the data folder, the scripts folder, README.md, and the .github folder), click **Commit changes**.
3. In the repo go to **Settings → Pages**. Under "Build and deployment" choose Source: *Deploy from a branch*,
   Branch: *main*, folder */ (root)*. Save. After a minute the page shows your address:
   `https://YOURNAME.github.io/dfw-atlas/`. That link is the live map.
4. To put it on your own site, add one line where the map should appear (WordPress: a "Custom HTML" block):
   ```html
   <iframe src="https://YOURNAME.github.io/dfw-atlas/" style="width:100%;height:90vh;border:0;border-radius:16px" title="DFW Neighborhood Atlas" loading="lazy"></iframe>
   ```
   A neighborhood can be linked directly: `.../dfw-atlas/#lakewood`, `#whitley-place`, `#vaquero`.
5. Custom domain later (e.g. `map.sucasamiguel.com`): Settings → Pages → Custom domain, then add the CNAME at
   your domain registrar. Optional.

## 2. Turn on the monthly housing refresh

1. Get a free Census API key: https://api.census.gov/data/key_signup.html (arrives by email in minutes).
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**. Name `CENSUS_KEY`,
   value = the key. Save.
3. **Actions** tab → "Refresh housing data" → **Run workflow**. It downloads Zillow's ZHVI/ZORI files and the
   Census figures, writes `data/housing.json`, and commits it. The map's Homes and People tabs fill in within a
   minute. It repeats on the 3rd of every month.
4. Optional sales data (median sale price, days on market): download `zip_code_market_tracker.tsv000.gz` from
   https://www.redfin.com/news/data-center/ and run locally:
   `python scripts/refresh_housing.py --census-key KEY --redfin zip_code_market_tracker.tsv000.gz`, then upload
   the new `data/housing.json`. (The file is ~1 GB, so it is not automated.)

## 3. Load real school ratings (run once a year, after TEA releases ratings in August)

Download three files:
* TEA campus directory with addresses/coordinates — tea.texas.gov → "School and District File" (AskTED) or the
  TEA campus location layer; export as CSV.
* TEA A–F campus ratings — txschools.gov → Data download (campus level CSV).
* NCES Private School Survey — nces.ed.gov/surveys/pss/pssdata.asp (school-level CSV).

Then: `python scripts/refresh_schools.py --campuses tea_campuses.csv --ratings tea_ratings.csv --private pss.csv`
and upload the new `data/schools.json`. If a column name in a download doesn't match, edit the `COLS` table at the
top of the script (the comments say which fields it needs).

## 4. Editing the map itself

Neighborhood names, zips, tiers, build tags, streets and lakes live in the generator project (areas.py,
neighborhoods_seed.py, arterials.py). Send edits and a new index.html is generated; the data files don't change.

## Attribution shown on the page
Zip areas: U.S. Census ZCTA. Home values and rents: Zillow Research. Sales: Redfin Data Center. Demographics:
U.S. Census ACS. School ratings: Texas Education Agency. Private schools: NCES. Neighborhood areas are stylized.
