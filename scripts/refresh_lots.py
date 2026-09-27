#!/usr/bin/env python3
"""Build data/lot_stats.json for the DFW Neighborhood Atlas: per-neighborhood (and per-zip) median lot
size, median lot ("land") value, and median home age, so the map can show them on hover without a
live per-parcel fetch.

Source (free, no key): the Texas Geographic Information Office's statewide parcel feed, built from
county appraisal district (CAD) data. One feed covers every county in the atlas (Dallas, Collin,
Denton, Tarrant, Rockwall, and any added later) -- no per-county integration needed.
  https://feature.geographic.texas.gov/arcgis/rest/services/Parcels/stratmap_land_parcels_48_most_recent/MapServer/0

For each neighborhood shape in data/geo.json, this queries every parcel whose centroid/geometry
intersects that shape (paginated, since the service caps replies at 2,000 records), normalizes each
parcel's reported area to acres (CADs report this in acres OR square feet -- sniffed from the
companion unit field, same logic as the map's own lot-click popup), and takes the median across the
neighborhood. Zip-level medians are a byproduct of the same fetch (bucketed by each parcel's own zip
attribute), so no extra queries are needed for the zip-code view.

"Lot price" here means the CAD's assessed *land* value (value of the lot itself, excluding the
house) -- the cleanest apples-to-apples figure across a mix of built and vacant lots. Total home
value (land + structure) is already shown elsewhere on the page from Zillow ZHVI.

Usage: python scripts/refresh_lots.py [--limit N]   (--limit caps how many neighborhoods to process,
for a quick test run; omit for the real monthly run.)
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


def lot_acres(rec):
    """Mirror the unit-sniffing logic in the map's own lot popup (template3.html lotPopupHTML)."""
    unit = str(rec.get('lgl_area_unit') or '').upper()
    acres = num(rec.get('legal_area'))
    if acres is not None and any(u in unit for u in ('S', 'FT', 'SQ')):
        acres = acres / 43560.0
    if acres is None or acres <= 0:
        g = num(rec.get('gis_area'))
        gu = str(rec.get('gis_area_unit') or '').upper()
        if g is not None:
            acres = g / 43560.0 if any(u in gu for u in ('S', 'FT', 'SQ')) else g
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, help='only process the first N neighborhoods (for a quick test run)')
    a = ap.parse_args()

    geo = json.load(open(os.path.join(DATA, 'geo.json')))
    hoods = geo['hoods']['features']
    if a.limit:
        hoods = hoods[:a.limit]
    print(f'{len(hoods)} neighborhoods to query')

    neighborhoods, zip_pool = {}, {}
    ok = 0
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
