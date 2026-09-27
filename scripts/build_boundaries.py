#!/usr/bin/env python3
"""Rebuild the atlas's neighborhood boundaries from real data instead of stylized shapes.

Background: the ~250 hand-curated neighborhood shapes in data/geo.json were always a stylized index
(organic blobs sized to a target area around a centroid -- the page's footer said so). That was fine
for a broad "where is Preston Hollow" view; it is not fine once someone reads the map at street level:
Highland Park's shape stopped short of US-75, "Devonshire" half-covered University Park, blobs
overlapped each other, and platted streets fell in the gaps between them with no neighborhood at all.

This script replaces that, in two stages that build on each other:

  --stage municipal   (runs anywhere -- needs only GitHub)
      * Fetches the exact legal city/town limits (Census TIGER, via a GitHub mirror -- the build box
        can't reach census.gov; GitHub Actions can and prefers the fresh TIGER file if reachable).
      * Every neighborhood that IS a town (Highland Park, University Park, Westlake, Trophy Club, Double
        Oak, Lucas, Parker, Murphy ...) becomes that town's exact polygon.
      * Every other neighborhood is clipped to its own city, so nothing labeled "Dallas" spills into the
        Park Cities and vice versa; overlaps between neighborhoods are removed (a town or legal shape
        wins over a stylized one; a smaller named neighborhood wins over the big region that engulfs it).
      * "Preston Center" is dropped as a neighborhood (it's a shopping district; the residential streets
        the old blob covered are University Park and Preston Hollow).
      * Writes a review report (data/boundary_report.csv + .md): what changed per neighborhood, which
        blobs were mostly outside their city (= mis-drawn), and where the remaining GAPS are inside each
        city -- with a Google Maps link per gap so they can be checked from a phone.

  --stage plats       (GitHub Actions only -- needs the parcel feed)
      * Takes the parcel pull from scripts/build_parcels.py (every lot in the five counties, with each
        lot's legal description) and snaps every neighborhood to LOT LINES: a neighborhood becomes the
        union of the platted subdivisions whose lots fall inside its stage-1 shape. That is what
        "accurate to the street number" means in practice -- the same plat names appraisers, title
        companies and Realtors use.
      * Lots inside a covered city that no named neighborhood claims are NOT left as gaps: they are
        grouped by their plat and added as their own small units ("<Plat name>", kind 'plat'), so every
        house is inside something and the something is right, even where nobody has coined a marketing
        name for it. Review those in the report; promoting a plat into a named neighborhood is a one-line
        edit to PLAT_TO_HOOD below.
      * Writes a PR-ready set of files; the workflow opens a pull request rather than pushing to main so
        the result is looked at before it goes live.

Usage:
  python scripts/build_boundaries.py --stage municipal [--dry-run]
  python scripts/build_boundaries.py --stage plats --parcels /tmp/parcels.ndjson [--dry-run]
"""
import argparse, csv, io, json, math, os, re, sys, urllib.request, zipfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')

MIRROR = 'https://raw.githubusercontent.com/generalpiston/geojson-us-city-boundaries/master/cities/tx/{slug}.json'
TIGER_PLACES = 'https://www2.census.gov/geo/tiger/TIGER2024/PLACE/tl_2024_48_place.zip'   # preferred when reachable
SIMPLIFY_DEG = 0.00003          # ~3 m; keeps legal lines legal while shaving file size
STREET_FUSE_M = 22              # plats stage: fuse lots across a street right-of-way (typical ROW 15-20 m)

# ---------------------------------------------------------------- which city each neighborhood belongs to
# Default city per region; a neighborhood listed in HOOD_CITY overrides its region's default. A city of
# None means "don't clip" (the shape genuinely spans several towns, e.g. Canyon Falls).
REGION_CITY = {
    'dallas': {'CEN': 'Dallas', 'EAST': 'Dallas', 'NE': 'Dallas', 'PC': 'Dallas', 'FN': 'Dallas', 'NW': 'Dallas',
               'WEST': 'Dallas', 'OC': 'Dallas', 'SO': 'Dallas'},
    'richplano': {'RICH': 'Richardson', 'WPLANO': 'Plano', 'CPLANO': 'Plano', 'EPLANO': 'Plano'},
    'frisco': {'SFRISCO': 'Frisco', 'NFRISCO': 'Frisco', 'PROSPER': 'Prosper', 'CELINA': 'Celina'},
    'mckinney': {'MCK': 'McKinney', 'ALLEN': 'Allen', 'ACRE': 'Fairview'},
    'corridor': {'IRVING': 'Irving', 'COPPELL': 'Coppell', 'GRAPEVINE': 'Grapevine', 'NWCUSTOM': None},
    'westlake-colleyville': {'WESTLAKE': 'Westlake', 'COLLEY': 'Colleyville'},
    'southlake': {'SOUTHLAKE': 'Southlake'},
    'rockwall': {'ROCKWALL': 'Rockwall', 'HEATH': 'Heath', 'FATE': 'Fate', 'ROWLETT': 'Rowlett', 'SUNNY': 'Sunnyvale', 'WYLIE': 'Wylie'},
}
HOOD_CITY = {
    'Highland Park': 'Highland Park', 'University Park': 'University Park', 'Cockrell Hill': 'Cockrell Hill',
    'Murphy': 'Murphy', 'Lucas': 'Lucas', 'Parker': 'Parker',
    'Flower Mound': 'Flower Mound', 'Highland Village': 'Highland Village', 'Double Oak': 'Double Oak',
    'Bartonville': 'Bartonville', 'Argyle': 'Argyle', 'Copper Canyon': 'Copper Canyon', 'Canyon Falls': None,
    'Northlake / Justin': 'Northlake|Justin',
    'Trophy Club': 'Trophy Club', 'Roanoke': 'Roanoke',
    'Sonoma Verde': 'McLendon-Chisholm', 'Downtown Sachse': 'Sachse', 'Woodbridge': 'Sachse',
    'Firewheel': 'Garland', 'Firewheel Town Center': 'Garland', 'East Garland / Duck Creek': 'Garland',
    'Inspiration': None,   # Wylie / St. Paul ETJ -- no single town polygon
    'Heritage Ranch': 'Fairview', 'Fairview acreage': 'Fairview',
    'Fields / PGA Frisco': 'Frisco',
}
# Neighborhoods that ARE a whole town: the town polygon replaces the stylized shape outright.
TOWN_AS_HOOD = {'Highland Park', 'University Park', 'Cockrell Hill', 'Murphy', 'Lucas', 'Parker', 'Flower Mound',
                'Highland Village', 'Double Oak', 'Bartonville', 'Argyle', 'Copper Canyon', 'Trophy Club', 'Roanoke',
                'Northlake / Justin'}
# "<Town> acreage" entries: the town polygon minus every other named neighborhood in that town.
TOWN_REMAINDER = {'Fairview acreage': 'Fairview', 'Heath acreage': 'Heath', 'Celina acreage': 'Celina',
                  'Sunnyvale acreage': 'Sunnyvale'}
DROP = {'Preston Center'}   # not a neighborhood -- a shopping district; its residential blocks are UP / Preston Hollow

# ---------------------------------------------------------------- plats stage: plat name -> neighborhood
# Exact-match overrides for the plats stage, when a subdivision's lots straddle two seeds or the plat
# should belong to a neighborhood whose stage-1 shape misses it. Keys are the plat name as the CAD writes
# it in the legal description (uppercase), values are atlas neighborhood names. Add to this after reading
# data/boundary_report.md from a real run.
PLAT_TO_HOOD = {
    # 'VOLK ESTATES': 'University Park',
}


# ---------------------------------------------------------------- helpers
def log(*a): print(*a, flush=True)

def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': 'dfw-atlas-builder/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def slug(name): return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

def load_cities(names, dry_run=False):
    """Exact place polygons keyed by name. Tries the fresh TIGER file first (Actions can reach it), else
    the per-city GitHub mirror (TIGER 2019 -- fine for built-out cities, a few years stale at the edges)."""
    from shapely.geometry import shape as shp
    from shapely.ops import unary_union
    out = {}
    want = set(names)
    try:
        import shapefile
        zb = fetch(TIGER_PLACES, timeout=300)
        zf = zipfile.ZipFile(io.BytesIO(zb)); base = next(n[:-4] for n in zf.namelist() if n.endswith('.shp'))
        sf = shapefile.Reader(shp=io.BytesIO(zf.read(base+'.shp')), dbf=io.BytesIO(zf.read(base+'.dbf')), shx=io.BytesIO(zf.read(base+'.shx')))
        fields = [f[0] for f in sf.fields[1:]]
        for sr in sf.iterShapeRecords():
            rec = dict(zip(fields, sr.record))
            if rec['NAME'] in want:
                out[rec['NAME']] = shp(sr.shape.__geo_interface__).buffer(0)
        log(f'  city limits: TIGER 2024 ({len(out)} of {len(want)} found)')
    except Exception as e:
        log(f'  TIGER file not reachable here ({str(e)[:60]}); using the GitHub mirror (TIGER 2019)')
    for n in sorted(want - set(out)):
        try:
            g = json.loads(fetch(MIRROR.format(slug=slug(n))))
            feats = g['features'] if g.get('type') == 'FeatureCollection' else [g]
            out[n] = unary_union([shp(f['geometry']) for f in feats]).buffer(0)
        except Exception as e:
            log(f'  !! no city polygon for {n}: {e}')
    return out

def city_for(area, h):
    c = HOOD_CITY.get(h['name'], REGION_CITY.get(area, {}).get(h['region']))
    return c

def sqmi(g): return g.area * 69.0 * 69.0 * math.cos(math.radians(32.9))

def gmaps(pt): return f'https://www.google.com/maps/search/?api=1&query={pt.y:.5f},{pt.x:.5f}'

def rep_point(g):
    try: return g.representative_point()
    except Exception: return g.centroid

def to_geojson_geom(g):
    from shapely.geometry import mapping
    return json.loads(json.dumps(mapping(g)))

def round_geom(gj, nd=5):
    def rr(c):
        if isinstance(c[0], (int, float)): return [round(c[0], nd), round(c[1], nd)]
        return [rr(x) for x in c]
    gj['coordinates'] = rr(gj['coordinates']); return gj


# ---------------------------------------------------------------- stage 1: municipal
def stage_municipal(dry_run=False):
    from shapely.geometry import shape as shp
    from shapely.ops import unary_union
    geo = json.load(open(os.path.join(DATA, 'geo.json')))
    meta = json.load(open(os.path.join(DATA, 'areas_meta.json')))
    atlas = json.load(open(os.path.join(DATA, 'atlas.json')))

    # every city we need a polygon for
    need = set()
    for area, a in meta['areas'].items():
        for h in a['neighborhoods']:
            c = city_for(area, h)
            if c: need.update(c.split('|'))
    for c in TOWN_REMAINDER.values(): need.add(c)
    log(f'stage municipal: {len(need)} cities/towns needed')
    cities = load_cities(need)
    if dry_run:
        for n in sorted(need): log(f'  {n}: {"ok, %.1f sq mi" % sqmi(cities[n]) if n in cities else "MISSING"}')
        return

    hood_geo = {f['properties']['gid']: f for f in geo['hoods']['features']}
    rows = []            # report rows
    new_shapes = {}      # gid -> shapely
    kind = {}            # gid -> 'town' | 'clipped' | 'remainder' | 'unclipped'

    # pass 1: towns and clips
    for area, a in meta['areas'].items():
        for h in a['neighborhoods']:
            gid = h['gid']; f = hood_geo[gid]
            old = shp(f['geometry']).buffer(0)
            if h['name'] in DROP:
                kind[gid] = 'dropped'; rows.append([area, h['name'], 'dropped', '', round(sqmi(old), 2), 0, 100, 'not a neighborhood (shopping district)']); continue
            c = city_for(area, h)
            if h['name'] in TOWN_AS_HOOD and c:
                polys = [cities[x] for x in c.split('|') if x in cities]
                if polys:
                    g = unary_union(polys); new_shapes[gid] = g; kind[gid] = 'town'
                    rows.append([area, h['name'], 'town limits', c, round(sqmi(old), 2), round(sqmi(g), 2), '', 'exact legal town boundary']); continue
            if h['name'] in TOWN_REMAINDER:
                new_shapes[gid] = old; kind[gid] = 'remainder'; continue   # finalized in pass 2
            if c and all(x in cities for x in c.split('|')):
                cp = unary_union([cities[x] for x in c.split('|')])
                g = old.intersection(cp).buffer(0)
                removed = 100 * (1 - g.area / old.area) if old.area else 0
                flag = ''
                if g.is_empty or removed > 60:
                    # either the shape was drawn in the wrong place, or the city has annexed since the city file's
                    # vintage (Celina, Prosper, Frisco all grew fast) -- keep the old shape whole and flag it rather
                    # than leave a sliver; the plats stage assigns lots by what is actually platted there
                    flag = 'MIS-DRAWN or ANNEXED SINCE: mostly outside its city polygon -- left unclipped; check on the map'
                    g = old; removed = 0
                elif removed > 15: flag = 'trimmed at city line'
                new_shapes[gid] = g; kind[gid] = 'clipped'
                rows.append([area, h['name'], 'clipped to city', c, round(sqmi(old), 2), round(sqmi(g), 2), round(removed), flag])
            else:
                new_shapes[gid] = old; kind[gid] = 'unclipped'
                rows.append([area, h['name'], 'unchanged', c or '', round(sqmi(old), 2), round(sqmi(old), 2), 0, 'spans several towns / no city polygon'])

    # pass 2: remove overlaps. Priority: town > clipped named (smaller first) > remainder. A smaller named
    # neighborhood wins over a large region that engulfs it (Preston Hollow vs Bluffview, Lake Highlands vs
    # L Streets), which is how people actually use the names.
    order = sorted(new_shapes, key=lambda g: (0 if kind[g] == 'town' else 2 if kind[g] == 'remainder' else 1, new_shapes[g].area))
    claimed_by_city = defaultdict(list)
    all_claimed = []
    gid_city = {}
    for area, a in meta['areas'].items():
        for h in a['neighborhoods']: gid_city[h['gid']] = city_for(area, h)
    for gid in order:
        g = new_shapes[gid]
        if kind[gid] == 'remainder':
            name = next(h['name'] for a in meta['areas'].values() for h in a['neighborhoods'] if h['gid'] == gid)
            town = TOWN_REMAINDER[name]
            if town in cities:
                g = cities[town]
                for og in claimed_by_city.get(town, []): g = g.difference(og)
                g = g.buffer(0)
                rows.append([gid_city_area(meta, gid), name, 'town remainder', town, '', round(sqmi(g), 2), '', 'town limits minus its named neighborhoods'])
        else:
            for og in all_claimed:
                if g.intersects(og):
                    g = g.difference(og)
            g = g.buffer(0)
        if kind[gid] != 'town' and not g.is_empty:
            # drop slivers left over from overlap removal
            if g.geom_type == 'MultiPolygon':
                parts = [p for p in g.geoms if sqmi(p) > 0.01]
                g = unary_union(parts) if parts else g
        new_shapes[gid] = g
        all_claimed.append(g)
        c = gid_city.get(gid)
        if c:
            for x in c.split('|'): claimed_by_city[x].append(g)

    # pass 3: stage 1 must not CREATE gaps. Where clipping/dropping/de-overlapping removed ground the old
    # shapes covered (the residential blocks the old "Preston Center" blob held, the half of "Devonshire"
    # that was really University Park), hand that ground to the neighbouring neighborhood in the same city
    # that shares the longest edge with it -- a stylized line moved to a legal one, never a hole. Ground
    # the old shapes never covered stays a gap (and is listed below) until the plats stage fills it with
    # what is actually platted there.
    gid_area = {h['gid']: area for area, a in meta['areas'].items() for h in a['neighborhoods']}
    old_by_city = defaultdict(list)
    for area, a in meta['areas'].items():
        for h in a['neighborhoods']:
            c = gid_city.get(h['gid'])
            if c and h['name'] not in TOWN_AS_HOOD:
                for x in c.split('|'): old_by_city[x].append(shp(hood_geo[h['gid']]['geometry']).buffer(0))
    candidates = {gid for gid in new_shapes if kind[gid] in ('clipped', 'unclipped') and not new_shapes[gid].is_empty}
    for city, olds in old_by_city.items():
        if city not in cities: continue
        lost = unary_union(olds).intersection(cities[city]).difference(unary_union(claimed_by_city[city])).buffer(0)
        parts = list(lost.geoms) if lost.geom_type == 'MultiPolygon' else ([lost] if not lost.is_empty else [])
        for part in parts:
            if sqmi(part) < 0.003: continue
            best, best_len = None, 0
            for gid in candidates:
                if city not in (gid_city.get(gid) or '').split('|'): continue
                g = new_shapes[gid]
                # the old shapes were drawn with a thin seam (~40 m) between neighbors, so test adjacency with a
                # tolerance a little wider than that seam
                near = part.buffer(0.0006)
                if not g.intersects(near): continue
                L = g.boundary.intersection(near).length
                if L > best_len: best, best_len = gid, L
            if best is not None:
                new_shapes[best] = unary_union([new_shapes[best], part]).buffer(0)
                claimed_by_city[city].append(part)
    # gap analysis per city: town polygon minus everything claimed there
    gaps = []
    for city, polys in claimed_by_city.items():
        if city not in cities: continue
        gap = cities[city].difference(unary_union(polys)).buffer(0)
        parts = list(gap.geoms) if gap.geom_type == 'MultiPolygon' else ([gap] if not gap.is_empty else [])
        parts = [p for p in parts if sqmi(p) >= 0.05]
        cov = 100 * (1 - gap.area / cities[city].area)
        gaps.append((city, round(cov), sorted(parts, key=lambda p: -p.area)))

    # write back
    for f in geo['hoods']['features']:
        gid = f['properties']['gid']
        if kind.get(gid) == 'dropped': continue
        g = new_shapes[gid].simplify(SIMPLIFY_DEG, preserve_topology=True)
        f['geometry'] = round_geom(to_geojson_geom(g))
        f['properties']['sqmi'] = round(sqmi(g), 2)
        f['properties']['city'] = (gid_city.get(gid) or '').replace('|', ' / ')
        f['properties']['kind'] = 'town' if kind[gid] == 'town' else 'named'
        rp = rep_point(g); f['properties']['lat'] = round(rp.y, 5); f['properties']['lon'] = round(rp.x, 5)
    geo['hoods']['features'] = [f for f in geo['hoods']['features'] if kind.get(f['properties']['gid']) != 'dropped']
    for area, a in meta['areas'].items():
        keep = []
        for h in a['neighborhoods']:
            if kind.get(h['gid']) == 'dropped': continue
            g = new_shapes[h['gid']]
            h['sqmi'] = round(sqmi(g), 2); h['bbox'] = [round(v, 5) for v in g.bounds]
            h['city'] = (gid_city.get(h['gid']) or '').replace('|', ' / ')
            h['kind'] = 'town' if kind[h['gid']] == 'town' else 'named'
            rp = rep_point(g); h['lat'] = round(rp.y, 5); h['lon'] = round(rp.x, 5)
            keep.append(h)
        a['neighborhoods'] = keep
        if keep: a['bounds'] = [min(h['bbox'][0] for h in keep), min(h['bbox'][1] for h in keep), max(h['bbox'][2] for h in keep), max(h['bbox'][3] for h in keep)]
    for a in atlas['areas'].values():
        a['neighborhoods'] = [h for h in a['neighborhoods'] if h['name'] not in DROP]
    json.dump(geo, open(os.path.join(DATA, 'geo.json'), 'w'), separators=(',', ':'))
    json.dump(meta, open(os.path.join(DATA, 'areas_meta.json'), 'w'))
    json.dump(atlas, open(os.path.join(DATA, 'atlas.json'), 'w'))
    write_report(rows, gaps, 'municipal')
    log(f'wrote geo.json ({len(geo["hoods"]["features"])} neighborhoods), areas_meta.json, atlas.json, data/boundary_report.*')

def gid_city_area(meta, gid):
    for area, a in meta['areas'].items():
        if any(h['gid'] == gid for h in a['neighborhoods']): return area
    return ''

def write_report(rows, gaps, stage):
    with open(os.path.join(DATA, 'boundary_report.csv'), 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['area', 'neighborhood', 'action', 'city', 'old_sqmi', 'new_sqmi', 'pct_removed', 'note'])
        for r in rows: w.writerow(r)
    L = [f'# Boundary rebuild report -- stage: {stage}', '',
         'Generated by scripts/build_boundaries.py. Every row is something to glance at, not necessarily something wrong.', '']
    flagged = [r for r in rows if r[7] and r[2] != 'town limits']
    L += ['## Neighborhoods to look at', '', '| Area | Neighborhood | What happened | City | Old sq mi | New sq mi | % removed | Note |', '|---|---|---|---|---|---|---|---|']
    for r in sorted(flagged, key=lambda r: (r[7].startswith('MIS') and 0 or 1, r[0], r[1])):
        L.append('| ' + ' | '.join(str(x) for x in r) + ' |')
    L += ['', '## Coverage and gaps by city', '', 'A gap is a part of the city no neighborhood claims. The plats stage fills every gap with the platted',
          'subdivisions actually there; until then this is the list of places that show no neighborhood.', '']
    for city, cov, parts in sorted(gaps, key=lambda x: x[1]):
        L.append(f'### {city} -- {cov}% covered, {len(parts)} gap(s) of 0.05 sq mi or more')
        for p in parts[:12]:
            rp = rep_point(p); L.append(f'- {sqmi(p):.2f} sq mi around [{rp.y:.4f}, {rp.x:.4f}]({gmaps(rp)})')
        if len(parts) > 12: L.append(f'- ... and {len(parts) - 12} smaller ones')
        L.append('')
    open(os.path.join(DATA, 'boundary_report.md'), 'w').write('\n'.join(L))


# ---------------------------------------------------------------- stage 2: plats
PLAT_CUT = re.compile(r'\b(BLK|BLOCK|LT|LOT|LOTS|TR|TRACT|ACS|ACRES|PT|UNIT|BLDG|PH|PHASE|SEC|SECTION|INST|REPLAT|REV|AMD)\b')
def plat_name(legal):
    """'PRESTON HOLLOW BLK 3 LT 12' -> 'PRESTON HOLLOW'. Lossy by design -- CADs write these by hand."""
    if not legal: return ''
    s = re.sub(r'[^A-Z0-9 &/\-]', ' ', str(legal).upper())
    s = PLAT_CUT.split(s, 1)[0]
    s = re.sub(r'\s+', ' ', s).strip(' -/&')
    s = re.sub(r'\b(ADDN|ADDITION|SUBD|SUBDIVISION|ESTS|ESTATES)$', lambda m: {'ADDN': 'ADDN', 'ADDITION': 'ADDN', 'SUBD': '', 'SUBDIVISION': '', 'ESTS': 'ESTATES', 'ESTATES': 'ESTATES'}[m.group(1)], s).strip()
    return s if len(s) >= 3 and not s[0].isdigit() else ''

def stage_plats(parcels_path, dry_run=False):
    from shapely.geometry import shape as shp, Point
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    geo = json.load(open(os.path.join(DATA, 'geo.json')))
    meta = json.load(open(os.path.join(DATA, 'areas_meta.json')))
    atlas = json.load(open(os.path.join(DATA, 'atlas.json')))
    hoods = geo['hoods']['features']
    seeds = [shp(f['geometry']).buffer(0) for f in hoods]
    seed_tree = STRtree(seeds)
    names = [f['properties']['name'] for f in hoods]
    towns = {i for i, f in enumerate(hoods) if f['properties'].get('kind') == 'town'}
    # cities we cover (for gap fill): union of stage-1 city polygons the hoods carry
    need = sorted({c for f in hoods for c in (f['properties'].get('city') or '').split(' / ') if c})
    cities = load_cities(need)
    city_tree = STRtree(list(cities.values())); city_names = list(cities.keys())

    log(f'stage plats: reading {parcels_path}')
    # per parcel: which seed (by lot centroid), plat name, city
    plat_lots = defaultdict(list)        # plat -> [parcel idx]
    plat_seed_votes = defaultdict(lambda: defaultdict(int))
    lot_geoms, lot_plat, lot_seed, lot_city = [], [], [], []
    n = 0
    with open(parcels_path) as fh:
        for line in fh:
            try: f = json.loads(line)
            except Exception: continue
            p = f.get('properties') or {}
            try: g = shp(f['geometry'])
            except Exception: continue
            if g.is_empty: continue
            c = g.representative_point()
            ci = [i for i in city_tree.query(c) if cities[city_names[i]].contains(c)]
            if not ci: continue                            # outside every covered city
            si = [i for i in seed_tree.query(c) if seeds[i].contains(c)]
            seed = min(si, key=lambda i: seeds[i].area) if si else -1   # smallest seed wins (matches stage-1 priority)
            plat = plat_name(p.get('legal_desc'))
            idx = len(lot_geoms)
            lot_geoms.append(g); lot_plat.append(plat); lot_seed.append(seed); lot_city.append(city_names[ci[0]])
            if plat:
                plat_lots[plat].append(idx); plat_seed_votes[plat][seed] += 1
            n += 1
            if dry_run and n >= 20000: break
    log(f'  {n:,} lots inside covered cities; {len(plat_lots):,} distinct plat names')
    if dry_run:
        top = sorted(plat_lots.items(), key=lambda kv: -len(kv[1]))[:40]
        log('  sample plat names (biggest first) and the neighborhood their lots mostly fall in:')
        for plat, lots in top:
            votes = plat_seed_votes[plat]; best = max(votes, key=votes.get)
            log(f'    {plat:<40} {len(lots):>6} lots  -> {names[best] if best >= 0 else "(no neighborhood)"} ({100*votes[best]//len(lots)}%)')
        return

    # assign every plat to one seed: explicit override, else the majority of its lots, if that majority is
    # clear (>=60%); a split plat stays lot-by-lot so a boundary that genuinely runs down a street holds
    plat_target = {}
    for plat, votes in plat_seed_votes.items():
        if plat in PLAT_TO_HOOD and PLAT_TO_HOOD[plat] in names:
            plat_target[plat] = names.index(PLAT_TO_HOOD[plat]); continue
        best = max(votes, key=votes.get); tot = sum(votes.values())
        if votes[best] / tot >= 0.6: plat_target[plat] = best
    members = defaultdict(list)     # seed idx -> lot idxs
    leftover = defaultdict(list)    # (city, plat) -> lot idxs   (gap fill)
    unplatted = []
    for i, g in enumerate(lot_geoms):
        plat = lot_plat[i]
        seed = plat_target.get(plat, lot_seed[i]) if plat else lot_seed[i]
        if seed >= 0 and seed not in towns: members[seed].append(i)
        elif seed < 0 and plat: leftover[(lot_city[i], plat)].append(i)
        elif seed < 0: unplatted.append(i)
        # lots inside a town-kind hood need nothing: the town polygon is already exact
    # a lot with no readable plat name and no seed joins the nearest named neighborhood in its city if one
    # is within ~150 m (a street's width or two); otherwise it is left to the city's own shape
    for i in unplatted:
        c = lot_geoms[i].representative_point()
        near = [(seeds[j].distance(c), j) for j in seed_tree.query(c.buffer(0.0015)) if j not in towns and lot_city[i] in (hoods[j]['properties'].get('city') or '').split(' / ')]
        if near:
            d, j = min(near)
            if d < 0.0015: members[j].append(i)

    def fuse(idxs):
        m = STREET_FUSE_M / 111320.0
        g = unary_union([lot_geoms[i] for i in idxs]).buffer(m).buffer(-m).buffer(0)
        return g.simplify(SIMPLIFY_DEG, preserve_topology=True)

    rows = []
    for si, idxs in members.items():
        g = fuse(idxs)
        old = seeds[si]
        # a named neighborhood that ends up with almost no lots (a park, a lake, an industrial tract, or a
        # feed gap for that county) keeps its stage-1 shape rather than collapsing to a few houses
        if len(idxs) < 20 or g.area < 0.15 * old.area:
            rows.append(['', names[si], 'kept stage-1 shape', hoods[si]['properties'].get('city', ''), round(sqmi(old), 2), round(sqmi(old), 2), '', f'only {len(idxs)} lots / {100*g.area/old.area:.0f}% of the seed -- not enough parcel data to snap; check the county feed or the seed'])
            continue
        hoods[si]['geometry'] = round_geom(to_geojson_geom(g)); hoods[si]['properties']['sqmi'] = round(sqmi(g), 2)
        hoods[si]['properties']['kind'] = 'named'; hoods[si]['properties']['lots'] = len(idxs)
        rp = rep_point(g); hoods[si]['properties']['lat'] = round(rp.y, 5); hoods[si]['properties']['lon'] = round(rp.x, 5)
        rows.append(['', names[si], 'snapped to lots', hoods[si]['properties'].get('city', ''), round(sqmi(old), 2), round(sqmi(g), 2), '', f'{len(idxs):,} lots'])
    # gap fill: one unit per (city, plat) with at least a handful of lots
    max_gid = max(f['properties']['gid'] for f in hoods)
    added = 0
    area_of_city = {}
    for area, a in meta['areas'].items():
        for h in a['neighborhoods']:
            for c in (h.get('city') or '').split(' / '):
                if c: area_of_city.setdefault(c, (area, h['region']))
    for (city, plat), idxs in sorted(leftover.items(), key=lambda kv: -len(kv[1])):
        if len(idxs) < 8 or city not in area_of_city: continue
        g = fuse(idxs); max_gid += 1; added += 1
        area, region = area_of_city[city]
        nm = plat.title().replace("'S", "'s")
        props = {'gid': max_gid, 'id': max_gid, 'area': area, 'name': nm, 'region': region, 'zips': [], 'tier': None, 'build': None,
                 'blurb': '', 'lat': round(rep_point(g).y, 5), 'lon': round(rep_point(g).x, 5), 'sqmi': round(sqmi(g), 2), 'city': city, 'kind': 'plat', 'lots': len(idxs)}
        hoods.append({'type': 'Feature', 'id': max_gid, 'properties': props, 'geometry': round_geom(to_geojson_geom(g))})
        mp = dict(props); mp['bbox'] = [round(v, 5) for v in g.bounds]; mp.pop('area')
        meta['areas'][area]['neighborhoods'].append(mp)
        atlas['areas'][area]['neighborhoods'].append({'name': nm, 'zips': [], 'lat': props['lat'], 'lon': props['lon']})
        rows.append([area, nm, 'added from plat', city, '', round(sqmi(g), 2), '', f'{len(idxs):,} lots, no named neighborhood claimed them'])
    log(f'  snapped {len(members)} neighborhoods to lot lines; added {added} plat units for otherwise-uncovered streets')
    # sync areas_meta geometry-derived fields for snapped hoods
    by_gid = {f['properties']['gid']: f for f in hoods}
    for a in meta['areas'].values():
        for h in a['neighborhoods']:
            f = by_gid.get(h['gid'])
            if f and f['properties'].get('kind') != 'plat':
                g = shp(f['geometry']); h['sqmi'] = f['properties']['sqmi']; h['bbox'] = [round(v, 5) for v in g.bounds]
                h['lat'] = f['properties']['lat']; h['lon'] = f['properties']['lon']; h['kind'] = f['properties'].get('kind'); h['lots'] = f['properties'].get('lots')
    try:
        rows += refresh_zips(geo, meta, atlas)
    except Exception as e:
        log(f'  zip outlines not refreshed ({str(e)[:80]}) -- the stylized zip shapes stay until this runs on GitHub Actions')
    json.dump(geo, open(os.path.join(DATA, 'geo.json'), 'w'), separators=(',', ':'))
    json.dump(meta, open(os.path.join(DATA, 'areas_meta.json'), 'w'))
    json.dump(atlas, open(os.path.join(DATA, 'atlas.json'), 'w'))
    write_report(rows, [], 'plats')
    log('wrote geo.json, areas_meta.json, atlas.json, data/boundary_report.*')


ZCTA_URL = 'https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip'
def refresh_zips(geo, meta, atlas):
    """Replace every stylized zip shape with the real Census ZCTA outline, and re-derive each neighborhood's
    zip list from what its (now lot-exact) shape actually overlaps. Census is reachable from GitHub Actions
    only."""
    from shapely.geometry import shape as shp
    sys.path.insert(0, HERE)
    from build_places import read_shp_zip, PREFILTER_BBOX
    log('  refreshing zip outlines from Census ZCTA ...')
    zctas = {rec['ZCTA5CE20']: g.buffer(0) for rec, g in read_shp_zip(fetch(ZCTA_URL, timeout=600), bbox_filter=PREFILTER_BBOX)}
    hoods = geo['hoods']['features']
    rows = []
    # neighborhood -> zips by overlap (>= 10% of the neighborhood, or its single largest)
    hood_zips = {}
    for f in hoods:
        g = shp(f['geometry']).buffer(0)
        ov = []
        for z, zg in zctas.items():
            if zg.intersects(g):
                a = zg.intersection(g).area
                if a > 0: ov.append((a / g.area, z))
        ov.sort(reverse=True)
        zs = [z for frac, z in ov if frac >= 0.10] or [z for frac, z in ov[:1]]
        hood_zips[f['properties']['gid']] = zs
        if zs != f['properties'].get('zips'):
            rows.append([f['properties']['area'], f['properties']['name'], 'zips re-derived', f['properties'].get('city', ''), '', '', '', f"{','.join(f['properties'].get('zips') or [])} -> {','.join(zs)}"])
        f['properties']['zips'] = zs
    for area, a in meta['areas'].items():
        for h in a['neighborhoods']: h['zips'] = hood_zips.get(h['gid'], h['zips'])
        a['zips'] = sorted({z for h in a['neighborhoods'] for z in h['zips']})
    for area, a in atlas['areas'].items():
        byname = {h['name']: h for h in meta['areas'][area]['neighborhoods']}
        for h in a['neighborhoods']:
            if h['name'] in byname: h['zips'] = byname[h['name']]['zips']
    # zip layer: one real outline per zip per area (the zip view is filtered by area)
    feats = []
    for area, a in meta['areas'].items():
        for z in a['zips']:
            if z not in zctas: continue
            zg = zctas[z].simplify(SIMPLIFY_DEG * 3, preserve_topology=True); rp = rep_point(zg)
            feats.append({'type': 'Feature', 'properties': {'zip': z, 'area': area, 'lat': round(rp.y, 5), 'lon': round(rp.x, 5)}, 'geometry': round_geom(to_geojson_geom(zg))})
    geo['zips']['features'] = feats
    log(f'  zip outlines: {len(feats)} real ZCTA shapes; {len(rows)} neighborhoods had their zip list change')
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', choices=['municipal', 'plats'], required=True)
    ap.add_argument('--parcels', help='newline-delimited GeoJSON from scripts/build_parcels.py (plats stage)')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if a.stage == 'municipal': stage_municipal(a.dry_run)
    else:
        if not a.parcels: ap.error('--parcels is required for the plats stage')
        stage_plats(a.parcels, a.dry_run)

if __name__ == '__main__':
    main()
