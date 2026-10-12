#!/usr/bin/env python3
"""Build the DFW Neighborhood Atlas's parcel/lot layer from TxGIO's bulk county downloads.

Why this exists (v15): the map's 'lots' layer used to query the Texas Geographic Information Office's live
ArcGIS parcel service once per map pan, straight from the visitor's browser. In September 2026 that server
started rejecting every query ("Requested operation is not supported by this service") while still
advertising query support in its own metadata -- so every click on a lot came back empty, for us and for
everyone else using it. Nothing in the page was wrong; the third party under it was.

The fix is to stop depending on that server at all. TxGIO publishes the SAME dataset -- StratMap Land
Parcels, every county appraisal district's roll normalized to one schema, CC0-licensed -- as plain zip
downloads (file geodatabase + shapefile per county) on its DataHub, served as static files from a completely
separate host. This script:

  1. asks the DataHub API which "Land Parcels" collection is newest (TxGIO publishes a new one each year),
  2. downloads the zips for the five DFW counties this atlas covers (Dallas, Collin, Denton, Tarrant,
     Rockwall -- about 1 GB in all),
  3. converts each with GDAL's ogr2ogr and normalizes the field names to the ones the page already uses
     (PARCEL_FIELDS below -- shapefile copies truncate names to 10 characters, the GDB copies don't; both
     are mapped), and
  4. writes one newline-delimited GeoJSON file that .github/workflows/refresh_parcels.yml turns into a
     single PMTiles file on this repo's own "parcels-data" GitHub Release. The page reads lot lines and
     click data straight from those tiles: no live lookups, nothing between a visitor and the map.

scripts/build_boundaries.py reads the same NDJSON to snap neighborhood lines to platted lots.

Needs: GDAL command-line tools (ogr2ogr, ogrinfo) -- `apt-get install gdal-bin` on Ubuntu, the workflow does
this. The sandbox that wrote this script can't reach TxGIO's hosts, so the first real run's --dry-run output
(in the workflow log) is the schema check: it downloads only the smallest county and prints the field names
it found and how they mapped.

Usage:
  python scripts/build_parcels.py --dry-run                  # discover the newest release, download Rockwall
                                                             # only, print field names + 3 sample records
  python scripts/build_parcels.py --out /tmp/parcels.ndjson  # full pull of all five counties
  python scripts/build_parcels.py --out x.ndjson --zip ROCKWALL=/path/to/local.zip   # test with a local zip
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, time, urllib.request, urllib.parse, urllib.error, zipfile

API = 'https://api.tnris.org/api/v1'
COLLECTION_NAME = 'Land Parcels'
# county name (as the page shows it) -> Census/TxGIO FIPS code (in the download file names)
COUNTIES = {'DALLAS': '48113', 'COLLIN': '48085', 'DENTON': '48121', 'TARRANT': '48439', 'ROCKWALL': '48397'}
# Same field list template3.html reads for the popup (PARCEL_FIELDS in the client). Every output feature
# carries exactly these keys, so the tiles need no renaming on the page.
PARCEL_FIELDS = ['prop_id', 'geo_id', 'legal_area', 'lgl_area_unit', 'gis_area', 'gis_area_unit', 'legal_desc',
                 'land_value', 'imp_value', 'mkt_value', 'situs_addr', 'situs_city', 'situs_zip', 'county',
                 'year_built', 'stat_land_use', 'loc_land_use', 'source', 'tax_year']
# Other spellings the same field arrives under: shapefile 10-character truncations, and a few variants seen
# in appraisal-district exports. Keys are lower-cased source names; values are the client's field name.
ALIASES = {
    'lgl_area_u': 'lgl_area_unit', 'legal_area_unit': 'lgl_area_unit', 'legal_area_units': 'lgl_area_unit',
    'gis_area_u': 'gis_area_unit', 'gis_area_units': 'gis_area_unit',
    'stat_land_': 'stat_land_use', 'state_land_use': 'stat_land_use', 'stat_landuse': 'stat_land_use',
    'loc_land_u': 'loc_land_use', 'local_land_use': 'loc_land_use', 'loc_landuse': 'loc_land_use',
    'situs_address': 'situs_addr', 'situs_addre': 'situs_addr', 'situs_st': 'situs_addr',
    'propid': 'prop_id', 'prop_id_': 'prop_id', 'property_id': 'prop_id', 'pid': 'prop_id',
    'geoid': 'geo_id', 'geo_id_': 'geo_id',
    'market_value': 'mkt_value', 'mktvalue': 'mkt_value', 'total_value': 'mkt_value',
    'improvement_value': 'imp_value', 'impvalue': 'imp_value', 'landvalue': 'land_value',
    'yr_built': 'year_built', 'yearbuilt': 'year_built',
    'legal_description': 'legal_desc', 'legal_des': 'legal_desc',
    'county_name': 'county', 'cnty': 'county', 'taxyear': 'tax_year', 'tax_yr': 'tax_year',
}
RETRIES = 4
UA = 'dfw-neighborhood-atlas/1.0 (+github actions; parcels refresh)'


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- DataHub API
def api_get(path, params=None):
    url = API + path + ('?' + urllib.parse.urlencode(params) if params else '')
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f'DataHub API failed after {RETRIES} tries: {last}\n  {url}')


def land_parcel_collections():
    """Every 'Land Parcels' collection on TxGIO's DataHub, newest first. TxGIO publishes one per year and keeps
    the older ones online, which is what makes the per-county fallback below possible."""
    r = api_get('/collections/', {'search': COLLECTION_NAME, 'limit': 50})
    rows = [c for c in r.get('results', []) if str(c.get('name', '')).strip().lower() == COLLECTION_NAME.lower()]
    if not rows:
        raise RuntimeError(f'no collection named {COLLECTION_NAME!r} on the DataHub API: {json.dumps(r)[:400]}')
    rows.sort(key=lambda c: (str(c.get('acquisition_date') or ''), str(c.get('publication_date') or '')), reverse=True)
    return [{'collection_id': c['collection_id'], 'name': c.get('name'), 'acquisition_date': c.get('acquisition_date'),
             'publication_date': c.get('publication_date'), 'license': c.get('license_name'), 'source': c.get('source_name')}
            for c in rows]


def newest_collection():
    return land_parcel_collections()[0]


def county_resource(collection_id, county):
    """Download URL + size for one county's zip in a collection."""
    r = api_get('/resources/', {'collection_id': collection_id, 'area_type_name': county.title(), 'area_type': 'county'})
    rows = [x for x in r.get('results', []) if str(x.get('area_type_name', '')).upper() == county
            and str(x.get('resource', '')).lower().endswith('.zip')]
    if not rows:
        # the API has one row per county; if it's missing (or the API's filter changes), fall back to the
        # documented file-name pattern so a run can still succeed -- and say so in the log
        fips = COUNTIES[county]
        r2 = api_get('/resources/', {'collection_id': collection_id, 'limit': 400})
        rows = [x for x in r2.get('results', []) if f'_{fips}_' in str(x.get('resource', ''))]
        if not rows:
            raise RuntimeError(f'{county}: no download listed for FIPS {fips} in collection {collection_id}')
        log(f'  {county}: found by FIPS code rather than name (API filter did not match)')
    x = rows[0]
    return {'url': x['resource'], 'bytes': x.get('filesize'), 'resource_id': x.get('resource_id')}


# ---------------------------------------------------------------- download / unpack
def download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    tmp = dest + '.part'
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, 'wb') as fh:
                total = int(r.headers.get('Content-Length') or 0); got = 0; mark = time.time()
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk: break
                    fh.write(chunk); got += len(chunk)
                    if time.time() - mark > 20:
                        log(f'    {got/1e6:,.0f} MB' + (f' of {total/1e6:,.0f}' if total else '')); mark = time.time()
            os.replace(tmp, dest)
            return dest
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (403, 404, 410):
                break   # the file isn't there; retrying won't change that (the caller may try an older release)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f'download failed after {RETRIES} tries: {last}\n  {url}')


def unpack(zip_path, into):
    os.makedirs(into, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(into)
    # prefer the file geodatabase (full field names, no 2 GB limit); fall back to the shapefile
    gdbs = [p for p in glob.glob(os.path.join(into, '**', '*.gdb'), recursive=True) if os.path.isdir(p)]
    if gdbs:
        return gdbs[0]
    shps = glob.glob(os.path.join(into, '**', '*.shp'), recursive=True)
    if shps:
        return shps[0]
    raise RuntimeError(f'no .gdb or .shp inside {zip_path}')


def pick_layer(src):
    """Name of the polygon layer in a GDAL source (a GDB can hold several tables)."""
    out = subprocess.run(['ogrinfo', '-ro', '-so', '-q', src], capture_output=True, text=True, check=True).stdout
    layers = []
    for line in out.splitlines():
        # ogrinfo prints "1: name (Multi Polygon)" for most drivers and "Layer: name (Multi Polygon)" for GDBs
        m = re.match(r'\s*(?:\d+|Layer):\s+(\S+)\s*(?:\((.*?)\))?', line)
        if m:
            layers.append((m.group(1), (m.group(2) or '').lower()))
    polys = [n for n, t in layers if 'polygon' in t] or [n for n, t in layers]
    if not polys:
        raise RuntimeError(f'no layers found in {src}: {out}')
    named = [n for n in polys if 'parcel' in n.lower()]
    return (named or polys)[0]


# ---------------------------------------------------------------- normalize
def norm_key(k):
    k = str(k).strip().lower()
    if k in PARCEL_FIELDS: return k
    if k in ALIASES: return ALIASES[k]
    k2 = re.sub(r'[^a-z0-9]+', '_', k).strip('_')
    if k2 in PARCEL_FIELDS: return k2
    return ALIASES.get(k2)


def clean_value(k, v):
    if v is None: return None
    if isinstance(v, str):
        v = v.strip()
        if v == '' or v.lower() in ('null', 'none'): return None
    if k in ('land_value', 'imp_value', 'mkt_value', 'legal_area', 'gis_area'):
        try:
            n = float(str(v).replace(',', '').replace('$', ''))
            return int(n) if n.is_integer() else round(n, 4)
        except ValueError:
            return None
    if k in ('year_built', 'tax_year'):
        m = re.search(r'\d{4}', str(v)); return int(m.group()) if m else None
    if k in ('prop_id', 'geo_id', 'situs_zip'):
        s = str(v); return s[:-2] if s.endswith('.0') else s
    return v


def convert(src, county, out_fh, limit=None):
    """ogr2ogr src -> GeoJSONSeq on stdout -> normalized NDJSON lines appended to out_fh.
    Returns (rows written, field-mapping report)."""
    layer = pick_layer(src)
    cmd = ['ogr2ogr', '-f', 'GeoJSONSeq', '/vsistdout/', src, layer, '-t_srs', 'EPSG:4326',
           '-lco', 'COORDINATE_PRECISION=6', '-lco', 'RS=NO', '-skipfailures', '-nlt', 'PROMOTE_TO_MULTI']
    if limit: cmd += ['-limit', str(limit)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1 << 16)
    n = 0; seen_src = None; mapping = {}; years = {}
    for line in proc.stdout:
        line = line.strip().lstrip('\x1e')
        if not line: continue
        try:
            f = json.loads(line)
        except json.JSONDecodeError:
            continue
        g = f.get('geometry')
        if not g or not g.get('coordinates'): continue
        src_props = f.get('properties') or {}
        if seen_src is None:
            seen_src = list(src_props.keys())
            for k in seen_src:
                nk = norm_key(k)
                if nk: mapping[nk] = k
        props = {k: None for k in PARCEL_FIELDS}
        for k, v in src_props.items():
            nk = norm_key(k)
            if nk and props.get(nk) is None:
                props[nk] = clean_value(nk, v)
        if not props['county']: props['county'] = county.title()
        else: props['county'] = re.sub(r'\s+county$', '', str(props['county']).strip(), flags=re.I).title()
        if not props['source']: props['source'] = 'TxGIO StratMap Land Parcels'
        if props['tax_year']: years[props['tax_year']] = years.get(props['tax_year'], 0) + 1
        out = {'type': 'Feature', 'properties': props, 'geometry': g}
        pid = props.get('prop_id')
        if pid and str(pid).isdigit(): out['id'] = int(pid)
        out_fh.write(json.dumps(out, separators=(',', ':'), ensure_ascii=False) + '\n')
        n += 1
    proc.stdout.close(); err = proc.stderr.read(); rc = proc.wait()
    if rc != 0 and n == 0:
        raise RuntimeError(f'ogr2ogr failed on {src} ({layer}): {err[-800:]}')
    if err.strip():
        log(f'    ogr2ogr notes: {err.strip().splitlines()[-1][:200]}')
    return n, {'layer': layer, 'source_fields': seen_src or [], 'mapped': mapping, 'years': years,
               'missing': [k for k in PARCEL_FIELDS if k not in mapping and k not in ('county', 'source')]}


def report_mapping(county, rep):
    log(f'  {county}: layer {rep["layer"]!r}, {len(rep["source_fields"])} source fields')
    log(f'    source fields: {", ".join(rep["source_fields"])}')
    log(f'    mapped: ' + ', '.join(f'{k}<-{v}' for k, v in rep['mapped'].items()))
    if rep['missing']:
        log(f'    !! not found in this county\'s file (popup shows a dash for these): {", ".join(rep["missing"])}')


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None, help='newline-delimited GeoJSON to write (required unless --dry-run)')
    ap.add_argument('--dry-run', action='store_true', help='discover the newest release, download the smallest county only, print field names + samples; write nothing')
    ap.add_argument('--counties', default=None, help='comma-separated subset of ' + ','.join(COUNTIES))
    ap.add_argument('--work', default=os.environ.get('RUNNER_TEMP') or '/tmp', help='scratch dir for downloads (each county is deleted after it is converted)')
    ap.add_argument('--zip', action='append', default=[], metavar='COUNTY=PATH', help='use a local zip for a county instead of downloading (testing)')
    ap.add_argument('--api', default=None, help='override the DataHub API base URL (testing)')
    ap.add_argument('--meta', default=None, help='also write a small JSON file describing the source release (collection id, dates, counts)')
    ap.add_argument('--keep', action='store_true', help='keep downloaded zips/extracted files')
    ap.add_argument('--skip-if-current', default=None, metavar='STATE_JSON',
                    help='path of the --meta file from the last successful run; if TxGIO has not published a newer release since, '
                         'print a notice, set skip=true in $GITHUB_OUTPUT and exit 0 without downloading anything')
    ap.add_argument('--force', action='store_true', help='rebuild even if --skip-if-current says nothing changed')
    ap.add_argument('--fallback-depth', type=int, default=3, help='if a county fails in the newest release, try up to this many older releases for that county (0 = never)')
    args = ap.parse_args()

    global API
    if args.api: API = args.api.rstrip('/')
    counties = [c.strip().upper() for c in args.counties.split(',')] if args.counties else list(COUNTIES)
    bad = [c for c in counties if c not in COUNTIES]
    if bad: ap.error(f'unknown counties {bad}; known: {list(COUNTIES)}')
    local = dict(kv.split('=', 1) for kv in args.zip)
    local = {k.upper(): v for k, v in local.items()}
    if not args.dry_run and not args.out: ap.error('--out is required unless --dry-run')
    for tool in ('ogr2ogr', 'ogrinfo'):
        if not shutil.which(tool): sys.exit(f'{tool} not found -- install GDAL (apt-get install gdal-bin)')

    meta = {'built': time.strftime('%Y-%m-%d'), 'counties': {}}
    coll = None; colls = []
    need_api = any(c not in local for c in counties)
    if need_api:
        colls = land_parcel_collections(); coll = colls[0]
        if len(colls) > 1:
            log('Older releases available as per-county fallback: ' + ', '.join(str(c['acquisition_date'])[:7] for c in colls[1:]))
        meta.update({k: coll[k] for k in ('collection_id', 'acquisition_date', 'publication_date', 'license', 'source')})
        log(f'Newest TxGIO {coll["name"]} release: acquired {coll["acquisition_date"]}, published {coll["publication_date"]}, '
            f'license {coll["license"]}, collection {coll["collection_id"]}')
        if args.skip_if_current and not args.force and not args.dry_run and os.path.exists(args.skip_if_current):
            try: prev = json.load(open(args.skip_if_current))
            except Exception: prev = {}
            if prev.get('collection_id') == coll['collection_id'] and prev.get('total') and not prev.get('fallbacks'):
                # (a build that had to fall back to an older release for some county is NOT 'current': it is
                # redone each run until every county comes from the newest release)
                log(f'Already built from this release on {prev.get("built")} ({prev.get("total"):,} parcels) -- nothing new '
                    f'from TxGIO, so nothing to download. Use --force (or the workflow\'s "force" box) to rebuild anyway.')
                if os.environ.get('GITHUB_OUTPUT'):
                    with open(os.environ['GITHUB_OUTPUT'], 'a') as fh: fh.write('skip=true\n')
                return
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as fh: fh.write('skip=false\n')

    if args.dry_run:
        log('--- dry run: nothing written or published ---')
        plan = []
        for c in counties:
            if c in local:
                plan.append((c, {'url': local[c], 'bytes': os.path.getsize(local[c])}))
            else:
                res = county_resource(coll['collection_id'], c); plan.append((c, res))
                log(f'  {c}: {res["url"]}  ({(res["bytes"] or 0)/1e6:,.0f} MB)')
        c, res = min(plan, key=lambda t: t[1]['bytes'] or 1e18)
        log(f'\nDownloading the smallest county ({c}) to check the schema...')
        work = os.path.join(args.work, f'parcels_{c.lower()}'); os.makedirs(work, exist_ok=True)
        zp = res['url'] if c in local else download(res['url'], os.path.join(work, 'src.zip'))
        src = unpack(zp, os.path.join(work, 'x'))
        import io
        buf = io.StringIO(); n, rep = convert(src, c, buf, limit=3)
        report_mapping(c, rep)
        for line in buf.getvalue().splitlines():
            f = json.loads(line); log('  sample: ' + json.dumps(f['properties'], separators=(',', ':')))
        if not args.keep: shutil.rmtree(work, ignore_errors=True)
        return

    def one_attempt(c, work, zp_or_none, release):
        """Download (unless a zip is given), unpack and convert one county from one release into a
        per-county temp file. Returns (rows, mapping report, temp path). Raises on any failure -- the temp
        file is then discarded, so a half-converted county can never leak into the output."""
        if zp_or_none is None:
            res = county_resource(release['collection_id'], c)
            log(f'{c}: downloading {res["url"]} ({(res["bytes"] or 0)/1e6:,.0f} MB)')
            zp = download(res['url'], os.path.join(work, f'src_{release["collection_id"][:8]}.zip'))
        else:
            zp = zp_or_none
        src = unpack(zp, os.path.join(work, 'x_' + release['collection_id'][:8] if release else 'x_local'))
        tmp = os.path.join(work, 'county.ndjson')
        with open(tmp, 'w') as tfh:
            n, rep = convert(src, c, tfh)
        if n == 0:
            raise RuntimeError('converted 0 parcels')
        return n, rep, tmp

    grand = 0; all_years = {}; meta['county_source'] = {}; meta['fallbacks'] = []
    with open(args.out, 'w') as fh:
        for c in counties:
            t0 = time.time()
            work = os.path.join(args.work, f'parcels_{c.lower()}'); os.makedirs(work, exist_ok=True)
            # newest release first; then, if that county's file is missing, won't download, or won't convert,
            # the same county from progressively older releases (TxGIO keeps them all online). A year-old roll
            # for one county beats a hole in the map for that county.
            attempts = [(local[c], None)] if c in local else [(None, r) for r in colls[:1 + max(0, args.fallback_depth)]]
            done = False
            for zp, release in attempts:
                try:
                    if zp is None and release is not coll:
                        log(f'{c}: trying the older {str(release["acquisition_date"])[:7]} release instead')
                    elif zp is not None:
                        log(f'{c}: using local zip {zp}')
                    n, rep, tmp = one_attempt(c, work, zp, release or coll)
                    with open(tmp) as tfh: shutil.copyfileobj(tfh, fh)
                    report_mapping(c, rep)
                    src_desc = {'collection_id': (release or coll or {}).get('collection_id'), 'acquisition_date': (release or coll or {}).get('acquisition_date'),
                                'publication_date': (release or coll or {}).get('publication_date')} if release or coll else {'local': True}
                    if release is not None and release is not coll:
                        src_desc['fallback'] = True; meta['fallbacks'].append(c.title())
                        log(f'::warning::{c}: built from the older {str(release["acquisition_date"])[:7]} TxGIO release (newest failed)')
                    meta['county_source'][c.title()] = src_desc
                    log(f'{c}: wrote {n:,} parcels in {time.time()-t0:,.0f}s')
                    meta['counties'][c.title()] = n
                    for y, k in rep['years'].items(): all_years[y] = all_years.get(y, 0) + k
                    grand += n; done = True
                    break
                except Exception as e:
                    log(f'{c}: FAILED ({str(release["acquisition_date"])[:7] if release else "local"}) -- {e}')
            if not done:
                log(f'{c}: FAILED in every release tried -- continuing with the remaining counties')
                meta['counties'][c.title()] = 0
            if not args.keep: shutil.rmtree(work, ignore_errors=True)
    meta['total'] = grand
    if all_years:
        meta['tax_year'] = max(all_years, key=all_years.get)   # the roll year most parcels carry
        meta['tax_year_min'], meta['tax_year_max'] = min(all_years), max(all_years)
    log(f'\nTOTAL parcels written: {grand:,} -> {args.out}')
    if args.meta:
        with open(args.meta, 'w') as fh: json.dump(meta, fh, indent=1)
        log(f'source description -> {args.meta}')
    if grand == 0:
        log('!! zero parcels written across every county -- exiting non-zero so the workflow does not publish an empty layer.')
        sys.exit(1)
    if any(v == 0 for v in meta['counties'].values()):
        log('!! at least one county produced nothing (see FAILED above). Exiting non-zero: a partial layer would silently '
            'show empty lots for a whole county. Re-run after checking the log; nothing was published.')
        sys.exit(2)


if __name__ == '__main__':
    main()
