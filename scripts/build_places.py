#!/usr/bin/env python3
"""One-time (or occasional -- Census updates these yearly) job: add every incorporated city and
census-designated place in the atlas's five counties (Dallas, Collin, Denton, Tarrant, Rockwall) to
the map as its own shape, using real Census cartographic boundaries -- not hand-drawn. Zip code(s)
and school district come from a spatial join against Census's own ZCTA and Unified School District
boundary files. All three sources are free, no key, no account.

This is purely additive: the ~236 hand-curated Dallas-area neighborhoods already in data/geo.json,
data/areas_meta.json and data/atlas.json are left completely alone. Anything whose name already
exists anywhere in the atlas is skipped, so nothing collides with (or duplicates) the hand-tuned
entries in the Richardson & Plano / Frisco / McKinney / Southlake / Rockwall areas.

What it can't give you (be upfront about this in the delivered map): a hand-written blurb, a price
tier, or a build-activity tag for ~150+ new places -- nobody's reviewed each of those individually.
Price tier fills in automatically once housing.json's zip-level Zillow data covers the new zip; blurb
and build tag are left blank (the panel already renders fine with either missing).

Needs network (downloads roughly 150-250MB of Census shapefiles across three files) and
`pip install pyshp shapely` -- meant to run on GitHub Actions (workflow: build_places.yml), not in a
network-sandboxed dev environment.

Usage: python scripts/build_places.py [--dry-run]
"""
import argparse, io, json, math, os, re, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')

COUNTIES = {'113': 'Dallas', '085': 'Collin', '121': 'Denton', '439': 'Tarrant', '397': 'Rockwall'}
PLACE_URL = 'https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_48_place_500k.zip'
UNSD_URL = 'https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_48_unsd_500k.zip'
ZCTA_URL = 'https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip'
# Generous bounding box around the 5 counties -- just a fast pre-filter so we don't build shapely
# geometry for all ~33,000 national ZCTAs, not the real spatial join (that's exact, below).
PREFILTER_BBOX = (-98.0, 32.0, -95.8, 33.8)

NEW_REGION_KEY = 'more'
NEW_REGION_NAME = 'More nearby cities'
NEW_REGION_COLOR = '#8C8578'


def dl(url):
    print('  GET', url)
    req = urllib.request.Request(url, headers={'User-Agent': 'dfw-atlas-builder/1.0'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def read_shp_zip(zip_bytes, bbox_filter=None):
    """Yield (record_dict, shapely_geometry) for every shape in a Census shapefile zip. If
    bbox_filter is given, shapes whose own bbox doesn't overlap it are skipped before building
    geometry (keeps the huge national ZCTA file cheap to scan)."""
    import shapefile
    from shapely.geometry import shape as shp_shape
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    base = next(n[:-4] for n in zf.namelist() if n.lower().endswith('.shp'))
    sf = shapefile.Reader(shp=io.BytesIO(zf.read(base + '.shp')), dbf=io.BytesIO(zf.read(base + '.dbf')),
                           shx=io.BytesIO(zf.read(base + '.shx')))
    for sr in sf.iterShapeRecords():
        shp = sr.shape
        if bbox_filter and hasattr(shp, 'bbox') and shp.bbox:
            b = shp.bbox
            if b[2] < bbox_filter[0] or b[0] > bbox_filter[2] or b[3] < bbox_filter[1] or b[1] > bbox_filter[3]:
                continue
        try:
            geom = shp_shape(shp.__geo_interface__)
        except Exception:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        yield sr.record.as_dict(), geom


def field(rec, *candidates):
    for c in candidates:
        if c in rec and rec[c] not in (None, ''):
            return rec[c]
    return None


def clean_district_name(name):
    if not name:
        return None
    n = name
    n = re.sub(r'\bConsolidated Independent School District\b', 'CISD', n)
    n = re.sub(r'\bIndependent School District\b', 'ISD', n)
    return n


def load_existing():
    geo = json.load(open(os.path.join(DATA, 'geo.json')))
    meta = json.load(open(os.path.join(DATA, 'areas_meta.json')))
    atlas = json.load(open(os.path.join(DATA, 'atlas.json')))
    existing_names = set()
    for area in meta['areas'].values():
        for h in area['neighborhoods']:
            existing_names.add(h['name'].strip().lower())
    max_gid = max((f['properties']['gid'] for f in geo['hoods']['features']), default=-1)
    return geo, meta, atlas, existing_names, max_gid


def nearest_area(lon, lat, meta):
    best, best_d = None, None
    for k, a in meta['areas'].items():
        b = a['bounds']
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        d = (cx - lon) ** 2 + (cy - lat) ** 2
        if best_d is None or d < best_d:
            best, best_d = k, d
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help="print what would be added, don't write files")
    a = ap.parse_args()

    geo, meta, atlas, existing_names, max_gid = load_existing()

    print('Downloading Census cartographic boundary files...')
    place_zip = dl(PLACE_URL)
    unsd_zip = dl(UNSD_URL)
    zcta_zip = dl(ZCTA_URL)

    print('Places in the 5 counties...')
    places = []
    for rec, geom in read_shp_zip(place_zip):
        cty = field(rec, 'COUNTYFP')
        if cty not in COUNTIES:
            continue
        name = field(rec, 'NAME')
        if not name or name.strip().lower() in existing_names:
            continue
        places.append({'name': name.strip(), 'county': COUNTIES[cty], 'geom': geom})
    print(f'  {len(places)} new places (after skipping ones already in the atlas)')
    if not places:
        print('Nothing to add.'); return

    union_bbox = None
    for p in places:
        b = p['geom'].bounds
        union_bbox = b if union_bbox is None else (min(union_bbox[0], b[0]), min(union_bbox[1], b[1]),
                                                     max(union_bbox[2], b[2]), max(union_bbox[3], b[3]))

    print('School districts overlapping that area...')
    districts = [(rec, geom) for rec, geom in read_shp_zip(unsd_zip, bbox_filter=union_bbox)]
    print(f'  {len(districts)} candidate districts')

    print('ZIP Code Tabulation Areas overlapping that area (this is the slow one; national file)...')
    zctas = [(rec, geom) for rec, geom in read_shp_zip(zcta_zip, bbox_filter=union_bbox)]
    print(f'  {len(zctas)} candidate ZCTAs')

    for p in places:
        g = p['geom']; area = g.area
        # zips: any ZCTA covering at least 15% of the place's area, largest first, capped at 3
        hits = []
        for rec, zg in zctas:
            if not g.bounds or not zg.bounds:
                continue
            if zg.bounds[2] < g.bounds[0] or zg.bounds[0] > g.bounds[2] or zg.bounds[3] < g.bounds[1] or zg.bounds[1] > g.bounds[3]:
                continue
            inter = g.intersection(zg).area
            if inter > 0:
                hits.append((inter, field(rec, 'ZCTA5CE20', 'ZCTA5CE10', 'ZCTA5CE', 'GEOID20', 'GEOID10')))
        hits.sort(key=lambda t: -t[0])
        zips = [z for _, z in hits if z][:3]
        if not zips and hits:
            zips = [hits[0][1]] if hits[0][1] else []
        p['zips'] = zips

        # district: single best-overlap unified school district
        best, best_overlap = None, 0
        for rec, dg in districts:
            if dg.bounds[2] < g.bounds[0] or dg.bounds[0] > g.bounds[2] or dg.bounds[3] < g.bounds[1] or dg.bounds[1] > g.bounds[3]:
                continue
            inter = g.intersection(dg).area
            if inter > best_overlap:
                best_overlap = inter; best = field(rec, 'NAME')
        p['district'] = clean_district_name(best)

        c = g.centroid
        p['lon'], p['lat'] = round(c.x, 5), round(c.y, 5)
        p['bbox'] = [round(v, 5) for v in g.bounds]
        p['sqmi'] = round(area * (111.32 * math.cos(math.radians(p['lat']))) * 110.54, 2)  # deg^2 -> km^2 (approx, equirect)
        p['sqmi'] = round(p['sqmi'] / 2.59, 2)  # km^2 -> sq mi
        p['area_key'] = nearest_area(p['lon'], p['lat'], meta)

    print(f"Assigning to areas: " + ', '.join(f"{k}={sum(1 for p in places if p['area_key']==k)}" for k in meta['areas']))

    if a.dry_run:
        for p in places[:20]:
            print(f"  {p['name']} ({p['county']}) -> area={p['area_key']} zips={p['zips']} district={p['district']} sqmi={p['sqmi']}")
        print(f'  ...({len(places)} total)')
        return

    gid = max_gid + 1
    by_area = {}
    for p in places:
        by_area.setdefault(p['area_key'], []).append(p)

    for area_key, plist in by_area.items():
        A = meta['areas'][area_key]
        if NEW_REGION_KEY not in A['regions']:
            A['regions'][NEW_REGION_KEY] = {'name': NEW_REGION_NAME, 'color': NEW_REGION_COLOR}
        bounds = A['bounds']
        for p in plist:
            props = {'gid': gid, 'id': re.sub(r'[^a-z0-9]+', '-', p['name'].lower()).strip('-'),
                      'area': area_key, 'name': p['name'], 'region': NEW_REGION_KEY, 'zips': p['zips'],
                      'tier': None, 'build': None, 'blurb': f"One of the incorporated cities/communities in {p['county']} County.",
                      'lat': p['lat'], 'lon': p['lon'], 'sqmi': p['sqmi'], 'inr': 200, 'district': p['district']}
            geo['hoods']['features'].append({'type': 'Feature', 'id': gid, 'properties': props,
                                              'geometry': json.loads(json.dumps(p['geom'].__geo_interface__))})
            hood_list_entry = dict(props); hood_list_entry['bbox'] = p['bbox']
            A['neighborhoods'].append(hood_list_entry)
            atlas['areas'][area_key]['neighborhoods'].append({'name': p['name'], 'zips': p['zips'], 'lat': p['lat'], 'lon': p['lon']})
            for z in p['zips']:
                if z not in A['zips']:
                    A['zips'].append(z)
            bounds = [min(bounds[0], p['bbox'][0]), min(bounds[1], p['bbox'][1]),
                      max(bounds[2], p['bbox'][2]), max(bounds[3], p['bbox'][3])]
            gid += 1
        A['bounds'] = bounds

    json.dump(geo, open(os.path.join(DATA, 'geo.json'), 'w'), separators=(',', ':'))
    json.dump(meta, open(os.path.join(DATA, 'areas_meta.json'), 'w'))
    json.dump(atlas, open(os.path.join(DATA, 'atlas.json'), 'w'))
    print(f'Added {len(places)} places across {len(by_area)} areas.')
    print('Updated data/geo.json, data/areas_meta.json and data/atlas.json.')
    print('index.html embeds data/areas_meta.json at build time, so it still needs regenerating --')
    print('run assemble3.py next (the workflow does this automatically) and commit index.html too,')
    print("or the new places will render as shapes on the map but won't be clickable yet.")


if __name__ == '__main__':
    main()
