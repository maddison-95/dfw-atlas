#!/usr/bin/env python3
"""Build data/lot_stats.json for the DFW Neighborhood Atlas: per-neighborhood (and per-zip) median lot
size, median lot ("land") value, and median home age, so the map can show them on hover without a
live per-parcel fetch.

Source (free, no key, CC0): county appraisal district (CAD) rolls as published by the Texas Geographic
Information Office (StratMap Land Parcels). Since v15 the numbers come from the parcel file
scripts/build_parcels.py downloads from TxGIO's bulk county zips (--parcels): every parcel's centroid is
matched to the neighborhood shapes in data/geo.json, each parcel's reported area is normalized to acres
(CADs report acres OR square feet -- sniffed from the companion unit field, same logic as the map's own
lot-click popup), and medians are taken per neighborhood and per zip. The whole five-county file is
processed in a couple of minutes with no network at all, which is why this now runs inside the
"Refresh parcel data" Action rather than the monthly refresh.

Without --parcels the script falls back to its original behavior: one paginated query per neighborhood
against TxGIO's live ArcGIS service. That service stopped answering queries in September 2026, so the
fallback is kept only for the day it comes back; the workflow always passes --parcels.

"Lot price" here means the CAD's assessed *land* value (value of the lot itself, excluding the
house) -- the cleanest apples-to-apples figure across a mix of built and vacant lots. Total home
value (land + structure) is already shown elsewhere on the page from Zillow ZHVI.

Usage: python scripts/refresh_lots.py --parcels /tmp/parcels.ndjson   (the normal, offline run)
       python scripts/refresh_lots.py [--limit N]                      (legacy live-query fallback)
"""
import argparse, json, os, statistics, sys, time, urllib.parse, urllib.request, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')
PARCELS_URL = 'https://feature.geographic.texas.gov/arcgis/rest/services/Parcels/stratmap_land_parcels_48_most_recent/MapServer/0/query'
FIELDS = ['legal_area', 'lgl_area_unit', 'gis_area', 'gis_area_unit', 'land_value', 'year_built', 'situs_zip']
PAGE = 2000
MAX_PARCELS_PER_HOOD = 20000   # safety cap so one huge/odd shape can't run away with the whole job
THIS_YEAR = datetime.date.today().year


def num(v):
    try:
        f = float(v)
        return f if f == f else None   # filters NaN
    except (TypeError, ValueError):
        return None


def is_sqft(unit):
    """Unit strings seen in CAD rolls: ACRES/AC/A vs SQFT/SF/SQ FT/FEET. (An earlier version tested for a bare
    'S', which also matched 'ACRES' and divided real acreages by 43,560.)"""
    u = str(unit or '').upper().strip()
    return bool(u) and 'AC' not in u and ('SQ' in u or 'FT' in u or 'FEET' in u or u in ('SF', 'F'))


def lot_acres(rec):
    """Mirror the unit-sniffing logic in the map's own lot popup (template3.html lotPopupHTML)."""
    unit = str(rec.get('lgl_area_unit') or '').upper()
    acres = num(rec.get('legal_area'))
    if acres is not None and is_sqft(unit):
        acres = acres / 43560.0
    if acres is None or acres <= 0:
        g = num(rec.get('gis_area'))
        gu = str(rec.get('gis_area_unit') or '').upper()
        if g is not None:
            acres = g / 43560.0 if is_sqft(gu) else g
    return acres if acres and acres > 0 else None


def rings_from_geometry(geom):
    t = geom.get('type')
    coords = geom.get('coordinates')
    rings = []
    if t == 'Polygon':
        rings.extend(coords)
    elif t == 'MultiPolygon':
        for poly in coords:
            rings.extend(poly)
    return rings


def fetch_page(rings, offset):
    params = {
        'f': 'json', 'geometryType': 'esriGeometryPolygon', 'spatialRel': 'esriSpatialRelIntersects',
        'inSR': '4326', 'outSR': '4326', 'returnGeometry': 'false',
        'geometry': json.dumps({'rings': rings, 'spatialReference': {'wkid': 4326}}),
        'outFields': ','.join(FIELDS), 'resultRecordCount': str(PAGE), 'resultOffset': str(offset),
    }
    url = PARCELS_URL + '?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def fetch_parcels(rings):
    out, offset = [], 0
    while True:
        try:
            data = fetch_page(rings, offset)
        except Exception as e:
            print('    fetch failed at offset', offset, ':', e)
            break
        feats = data.get('features') or []
        if data.get('error'):
            print('    server error:', data['error'])
            break
        out.extend(f['attributes'] for f in feats)
        if len(feats) < PAGE or len(out) >= MAX_PARCELS_PER_HOOD or not data.get('exceededTransferLimit'):
            break
        offset += PAGE
        time.sleep(0.15)
    return out


def stats_from_parcels(parcels):
    acres = [a for a in (lot_acres(p) for p in parcels) if a is not None and a < 20]  # drop assemblages/ranches
    land = [v for v in (num(p.get('land_value')) for p in parcels) if v and v > 0]
    yrs = [v for v in (num(p.get('year_built')) for p in parcels) if v and 1850 < v <= THIS_YEAR]
    if not parcels:
        return None
    out = {'n_parcels': len(parcels)}
    if acres:
        out['median_lot_acres'] = round(statistics.median(acres), 3)
        out['median_lot_sqft'] = round(statistics.median(acres) * 43560)
    if land:
        out['median_land_value'] = round(statistics.median(land))
    if yrs:
        y = statistics.median(yrs)
        out['median_year_built'] = int(y)
        out['median_home_age'] = int(THIS_YEAR - y)
    return out


def ring_centroid(geom):
    """Cheap, good-enough interior point for a lot: the vertex mean of its largest ring. Lots are small and
    nearly convex, so this lands inside the parcel; shapely isn't needed per feature."""
    t, c = geom.get('type'), geom.get('coordinates')
    if t == 'Polygon': ring = c[0]
    elif t == 'MultiPolygon': ring = max((poly[0] for poly in c), key=len)
    else: return None
    n = len(ring) - 1 if len(ring) > 1 and ring[0] == ring[-1] else len(ring)
    if n <= 0: return None
    return sum(p[0] for p in ring[:n]) / n, sum(p[1] for p in ring[:n]) / n


def from_parcel_file(path, hoods):
    """Offline path: match every parcel in the NDJSON file to the neighborhood shapes by centroid.
    Returns (neighborhoods dict, zips dict, parcels read)."""
    import numpy as np, shapely
    from shapely import STRtree
    from shapely.geometry import shape
    polys = []
    for f in hoods:
        try: g = shape(f['geometry']); polys.append(g if g.is_valid else g.buffer(0))
        except Exception: polys.append(shapely.Polygon())
    tree = STRtree(polys)
    xs, ys, recs = [], [], []
    with open(path) as fh:
        for line in fh:
            try: f = json.loads(line)
            except ValueError: continue
            g = f.get('geometry'); p = f.get('properties') or {}
            if not g: continue
            c = ring_centroid(g)
            if not c: continue
            xs.append(c[0]); ys.append(c[1])
            recs.append({k: p.get(k) for k in FIELDS})
    print(f'  {len(recs):,} parcels read from {path}')
    if not recs:
        return {}, {}, 0
    pts = shapely.points(np.column_stack([np.array(xs), np.array(ys)]))
    pt_idx, hood_idx = tree.query(pts, predicate='within')
    per_hood = {}
    for pi, hi in zip(pt_idx.tolist(), hood_idx.tolist()):
        per_hood.setdefault(hi, []).append(recs[pi])
    neighborhoods = {}
    for hi, parcels in per_hood.items():
        st = stats_from_parcels(parcels)
        if st:
            neighborhoods[hoods[hi]['properties']['name']] = st
    zip_pool = {}
    for rec in recs:
        z = str(rec.get('situs_zip') or '')[:5]
        if z and z.isdigit():
            zip_pool.setdefault(z, []).append(rec)
    zips = {z: st for z, parcels in zip_pool.items() if (st := stats_from_parcels(parcels)) and st['n_parcels'] >= 25}
    return neighborhoods, zips, len(recs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, help='only process the first N neighborhoods (for a quick test run)')
    ap.add_argument('--parcels', default=None, help='newline-delimited GeoJSON from scripts/build_parcels.py; computes everything offline')
    ap.add_argument('--meta', default=None, help='the --meta JSON written by build_parcels.py; used to describe the release in the output')
    a = ap.parse_args()
    source_note = None
    if a.meta and os.path.exists(a.meta):
        m = json.load(open(a.meta))
        source_note = ('County appraisal districts via the Texas Geographic Information Office (StratMap Land Parcels'
                       + (f", {m['tax_year']} rolls" if m.get('tax_year') else '')
                       + (f", release published {m['publication_date']}" if m.get('publication_date') else '') + ')')

    geo = json.load(open(os.path.join(DATA, 'geo.json')))
    hoods = geo['hoods']['features']
    if a.limit:
        hoods = hoods[:a.limit]

    neighborhoods, zip_pool = {}, {}
    ok = 0
    if a.parcels:
        print(f'{len(hoods)} neighborhoods; matching parcels from {a.parcels}')
        neighborhoods, zips, nread = from_parcel_file(a.parcels, hoods)
        ok = len(neighborhoods)
        out = {
            'asof': datetime.date.today().isoformat(),
            'generated': datetime.date.today().isoformat(),
            'source': source_note or 'County appraisal districts via the Texas Geographic Information Office (StratMap Land Parcels, bulk county files)',
            'note': 'median_land_value is the CAD-assessed land (lot-only) value, not the total home value; '
                    'medians are computed from all parcels whose centroid falls inside the neighborhood shape, '
                    'which can include some non-residential parcels.',
            'neighborhoods': neighborhoods,
            'zips': zips,
        }
        os.makedirs(DATA, exist_ok=True)
        json.dump(out, open(os.path.join(DATA, 'lot_stats.json'), 'w'))
        print(f'wrote data/lot_stats.json: {ok}/{len(hoods)} neighborhoods, {len(zips)} zips, from {nread:,} parcels')
        if ok == 0:
            sys.exit('no neighborhood matched any parcel -- is the parcel file empty or in a different projection?')
        return

    print(f'{len(hoods)} neighborhoods to query (live fallback)')
    for i, f in enumerate(hoods):
        p = f['properties']
        name, area = p['name'], p['area']
        rings = rings_from_geometry(f['geometry'])
        if not rings:
            continue
        print(f'  [{i+1}/{len(hoods)}] {name} ({area})...', end=' ')
        parcels = fetch_parcels(rings)
        print(f'{len(parcels)} parcels')
        st = stats_from_parcels(parcels)
        if st:
            neighborhoods[name] = st
            ok += 1
        for rec in parcels:
            z = str(rec.get('situs_zip') or '')[:5]
            if z and z.isdigit():
                zip_pool.setdefault(z, []).append(rec)
        time.sleep(0.15)

    zips = {}
    for z, parcels in zip_pool.items():
        st = stats_from_parcels(parcels)
        if st:
            zips[z] = st

    out = {
        'asof': datetime.date.today().isoformat(),
        'generated': datetime.date.today().isoformat(),
        'source': 'County appraisal districts via the Texas Geographic Information Office (StratMap statewide parcels)',
        'note': 'median_land_value is the CAD-assessed land (lot-only) value, not the total home value; '
                'medians are computed from all parcels whose CAD outline falls inside the (stylized) '
                'neighborhood shape, which can include some non-residential parcels.',
        'neighborhoods': neighborhoods,
        'zips': zips,
    }
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, 'lot_stats.json'), 'w'))
    print(f'wrote data/lot_stats.json: {ok}/{len(hoods)} neighborhoods, {len(zips)} zips')


if __name__ == '__main__':
    main()
