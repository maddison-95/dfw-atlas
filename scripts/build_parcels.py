#!/usr/bin/env python3
"""Build a self-hosted parcel/lot layer for the DFW Neighborhood Atlas.

Why this exists: template3.html's live 'lots' layer queries the Texas Geographic Information Office's
statewide StratMap parcels feed (feature.geographic.texas.gov) once per map pan/zoom, direct from the
visitor's browser. That's the same feed the five DFW-area county appraisal districts (Dallas/DCAD, Collin,
Denton, Tarrant, Rockwall) publish their own rolls into -- TxGIO republishes it as one already-normalized,
statewide service, which is why the client already asks it for exactly PARCEL_FIELDS below. Querying it live
on every pan means: a third party's uptime and rate limits sit between a visitor and the map, a busy area
hits the server's ~2000-feature cap ("zoom in more"), and every visitor re-downloads the same lines.

This script instead pulls the same feed for the five DFW counties ONCE (on a schedule, not per visitor),
and the workflow that runs it (.github/workflows/refresh_parcels.yml) tiles the result into a single
PMTiles file and publishes it as a GitHub Release asset. Once data/config.json's parcels_pmtiles_url points
at that asset, template3.html's switchLotsToStatic() swaps the live per-pan query for the static tiles --
same layer ids, same field names, same click/hover/popup code, nothing else on the page changes. Until that
config value is filled in, the site keeps working exactly as it does today (this is purely additive).

This has to run somewhere that can actually reach feature.geographic.texas.gov -- the sandbox that wrote
this script cannot (every outside host except pip/npm/GitHub is blocked here), so the county/field
assumptions below are the best fit given the client's already-proven PARCEL_FIELDS and TxGIO's documented
query API, not something this session could verify end-to-end. --dry-run is exactly for that first real
run: it fetches a handful of raw records with NO writes/uploads, and prints them, so the workflow log itself
becomes the schema check on first use -- if county names, a field name, or the row counts look off, fix the
COUNTIES/PARCEL_FIELDS constants below and re-run before ever trusting the full pull.

Usage:
  python scripts/build_parcels.py --dry-run                 # sample records + row-count estimate only, no files
  python scripts/build_parcels.py --out /tmp/parcels.ndjson  # full pull -> newline-delimited GeoJSON
"""
import argparse, json, sys, time, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_SERVICE = 'https://feature.geographic.texas.gov/arcgis/rest/services/Parcels/stratmap_land_parcels_48_most_recent/MapServer/0/query'
SERVICE = DEFAULT_SERVICE  # overridable via --service, for testing against a mock endpoint
# Same field list template3.html already asks this exact service for (PARCEL_FIELDS in the client) --
# keeping these identical means the tiles need zero field-renaming/mapping to work with the existing
# popup/lot-size code once switchLotsToStatic() points the 'lots' source at them instead.
PARCEL_FIELDS = 'prop_id,geo_id,legal_area,lgl_area_unit,gis_area,gis_area_unit,legal_desc,land_value,imp_value,mkt_value,situs_addr,situs_city,situs_zip,county,year_built,stat_land_use,loc_land_use,source,tax_year'
# The five DFW-area appraisal districts this atlas covers. Matched with LIKE/UPPER rather than an exact
# string so "DALLAS", "Dallas", or "DALLAS COUNTY" (whichever way TxGIO's own 'county' field happens to be
# populated) all match -- confirm the actual stored format from a --dry-run's sample output and tighten
# this if it ever needs to be more precise.
COUNTIES = ['DALLAS', 'COLLIN', 'DENTON', 'TARRANT', 'ROCKWALL']
PAGE_SIZE = 2000          # matches the server's observed maxRecordCount (same value the live client uses)
WORKERS = 6               # modest parallelism across offset pages -- this is a quarterly batch job, not a
                          # per-visitor request, so a handful of concurrent reads is a reasonable citizen
RETRIES = 4

def _get(params, timeout=60):
    url = SERVICE + '?' + urllib.parse.urlencode(params)
    last = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f'giving up after {RETRIES} tries: {last}\n  {url}')

def county_where(name):
    return f"UPPER(county) LIKE '%{name}%'"

def count_for(name):
    r = _get({'where': county_where(name), 'returnCountOnly': 'true', 'f': 'json'})
    if 'count' not in r:
        raise RuntimeError(f'county count query failed for {name}: {r}')
    return r['count']

def sample(name, n=3):
    r = _get({'where': county_where(name), 'outFields': PARCEL_FIELDS, 'outSR': '4326', 'f': 'json', 'resultRecordCount': n})
    return r.get('features', [])

def fetch_page(name, offset):
    params = {'where': county_where(name), 'outFields': PARCEL_FIELDS, 'outSR': '4326', 'f': 'geojson',
              'resultOffset': offset, 'resultRecordCount': PAGE_SIZE, 'geometryPrecision': 6}
    r = _get(params, timeout=90)
    return r.get('features', [])

def pull_county(name, out_fh, log):
    total = count_for(name)
    log(f'{name}: {total:,} parcels reported by the service')
    offsets = list(range(0, total, PAGE_SIZE))
    got = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_page, name, off): off for off in offsets}
        for fut in as_completed(futs):
            off = futs[fut]
            try:
                feats = fut.result()
            except Exception as e:
                log(f'  !! {name} offset {off}: {e} -- skipping this page (continuing with the rest)')
                continue
            for f in feats:
                # a stable, county-scoped feature id: MapServer object ids aren't guaranteed unique across
                # the whole statewide layer, but prop_id is unique within a county's roll
                out_fh.write(json.dumps(f, separators=(',', ':')) + '\n')
            got += len(feats)
    log(f'{name}: wrote {got:,} of {total:,} (any gap above is a page that failed after {RETRIES} retries)')
    return got

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None, help='Path to write newline-delimited GeoJSON features (required unless --dry-run)')
    ap.add_argument('--dry-run', action='store_true', help='Print a few sample records + row-count estimate per county; write nothing')
    ap.add_argument('--counties', default=None, help='Comma-separated override of the county list (for testing one county at a time)')
    ap.add_argument('--service', default=None, help='Override the ArcGIS query endpoint (for testing against a mock server)')
    args = ap.parse_args()
    counties = [c.strip().upper() for c in args.counties.split(',')] if args.counties else COUNTIES
    if args.service:
        global SERVICE
        SERVICE = args.service

    def log(msg): print(msg, flush=True)

    if args.dry_run:
        log('--- dry run: no files written, no tiles built, no upload ---')
        for name in counties:
            try:
                feats = sample(name)
                total = count_for(name)
                log(f'\n{name}: ~{total:,} parcels reported')
                for f in feats:
                    log(f'  sample attrs: {json.dumps(f.get("attributes", f.get("properties", {})), separators=(",", ":"))}')
                if not feats:
                    log(f'  !! no sample rows came back -- check the county_where() match for {name}')
            except Exception as e:
                log(f'{name}: FAILED -- {e}')
        return

    if not args.out:
        ap.error('--out is required unless --dry-run')

    grand_total = 0
    with open(args.out, 'w') as fh:
        for name in counties:
            try:
                grand_total += pull_county(name, fh, log)
            except Exception as e:
                log(f'{name}: FAILED entirely -- {e} -- continuing with the remaining counties')
    log(f'\nTOTAL parcels written: {grand_total:,} -> {args.out}')
    if grand_total == 0:
        log('!! zero parcels written across every county -- something is wrong (service down, where-clause '
            'mismatch, or field names changed). Exiting non-zero so the workflow does not publish an empty layer.')
        sys.exit(1)

if __name__ == '__main__':
    main()
