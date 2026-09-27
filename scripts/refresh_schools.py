#!/usr/bin/env python3
"""Build data/schools.json for the DFW Neighborhood Atlas.

Three sources, none needing a manual download every cycle except the two noted:
1. Public/charter school LOCATIONS + grade span + district + magnet/charter flags:
   pulled automatically from the NCES EDGE Open Data REST feed (no key, no signup).
   https://nces.ed.gov/opengis/rest/services/K12_School_Locations/
2. Public/charter A-F RATINGS (Texas doesn't publish this via any API): a CSV you
   download once per year from TEA's "Accountability Data Downloads" page
   (Report Level: Campus, Category: Accountability Summary) and commit to the repo
   as data-sources/tea_ratings.csv. Pass its path with --ratings.
3. Private schools: a CSV from the NCES Private School Survey (PSS), downloaded
   once every ~2 years (https://nces.ed.gov/surveys/pss/pssdata.asp -> Text/CSV
   file for the latest year) and committed to the repo as data-sources/pss.csv.
   Pass its path with --private. Optional -- omit to skip private schools.

Usage:
  python scripts/refresh_schools.py --ratings data-sources/tea_ratings.csv --private data-sources/pss.csv
Only campuses whose zip is in the atlas zip list (plus neighboring same-prefix zips)
are kept. Private schools have no TEA rating (Texas doesn't rate them).
"""
import argparse, csv, io, json, os, re, sys, datetime, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')

# NCES EDGE Open Data: public school locations + admin attributes, no key required.
# Most recent geocoded public-school layer (updated occasionally by NCES; bump the
# year suffix here when NCES publishes a newer one).
NCES_PUBLIC_LAYER = "https://nces.ed.gov/opengis/rest/services/K12_School_Locations/EDGE_GEOCODE_PUBLICSCH_2122/MapServer/0/query"

# Column-name candidates per field (first match wins, case-insensitive).
COLS = {
    'rating': {
        'id': ['CAMPUS', 'Campus #', 'Campus Number', 'CAMPUS_ID', 'Campus'],
        'rating': ['C_RATING', 'Rating', 'Overall Rating', 'RATING', '2026 Overall Rating', '2025 Rating', '2024 Rating'],
        'year': ['YEAR', 'Year', 'Rating Year'],
    },
    'private': {
        'name': ['PINST', 'School Name', 'NAME'],
        'city': ['PCITY', 'City'],
        'zip': ['PZIP', 'Zip'],
        'low': ['LOGR2024', 'LOGR22', 'LOGR20', 'Low Grade', 'LOGR'],
        'high': ['HIGR2024', 'HIGR22', 'HIGR20', 'High Grade', 'HIGR'],
        'lat': ['LATITUDE24', 'LATITUDE22', 'LATITUDE20', 'LATITUDE', 'Latitude'],
        'lon': ['LONGITUDE24', 'LONGITUDE22', 'LONGITUDE20', 'LONGITUDE', 'Longitude'],
    },
}

GRADE_ORDER = ['PK', 'KG', 'K', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12']

def pick(row, cands):
    keys = {k.lower(): k for k in row}
    for c in cands:
        if c.lower() in keys:
            v = row[keys[c.lower()]]
            return v.strip() if isinstance(v, str) else v
    return None

def norm_grade(g):
    if g is None: return None
    g = str(g).strip().upper().replace('EE', 'PK').replace('KINDERGARTEN', 'K').replace('PRE-K', 'PK')
    g = {'PK': 'PK', 'K': 'K', 'KG': 'K'}.get(g, g)
    if g.isdigit(): g = g.zfill(2)
    return g

def level(low, high):
    lo, hi = norm_grade(low), norm_grade(high)
    def idx(g): return GRADE_ORDER.index(g) if g in GRADE_ORDER else None
    a, b = idx(lo), idx(hi)
    if a is None or b is None: return 'K12'
    if b <= GRADE_ORDER.index('06'): return 'ES'
    if a >= GRADE_ORDER.index('06') and b <= GRADE_ORDER.index('09'): return 'MS'
    if a >= GRADE_ORDER.index('09'): return 'HS'
    if a <= GRADE_ORDER.index('05') and b <= GRADE_ORDER.index('08'): return 'ES'
    return 'K12'

def grades_label(low, high):
    lo, hi = norm_grade(low), norm_grade(high)
    if not lo or not hi: return ''
    f = lambda g: g.lstrip('0') if g and g[0] == '0' else g
    return f'{f(lo)}-{f(hi)}'

def read_csv_flex(path):
    with open(path, newline='', encoding='utf-8', errors='ignore') as f:
        sample = f.read(4096); f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',\t|')
        except csv.Error:
            dialect = csv.excel
        return list(csv.DictReader(f, dialect=dialect))

def load_zips():
    a = json.load(open(os.path.join(DATA, 'atlas.json')))
    zips = set()
    for area in a['areas'].values():
        for h in area['neighborhoods']: zips.update(h['zips'])
    prefixes = {z[:3] for z in zips}
    return zips, prefixes

def fetch_nces_public(zips, prefixes):
    """Query the NCES EDGE public-school layer for TX schools, then keep only
    the ones in/near our zip list. Returns a list of school dicts."""
    where = "STATE='TX'"
    params = {
        'where': where, 'outFields': '*', 'f': 'geojson',
        'outSR': '4326', 'resultRecordCount': '20000',
    }
    url = NCES_PUBLIC_LAYER + '?' + urllib.parse.urlencode(params)
    print('  GET (NCES public schools)', url[:120], '...')
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            gj = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print('  NCES fetch failed:', e)
        print('  (this step only works where the network can reach nces.ed.gov -- e.g. the GitHub Action; it is expected to fail in a sandboxed dev run)')
        return []
    out = []
    name_c = ['NAME', 'SCH_NAME', 'SCHNAM', 'SCHOOL_NAME']
    dist_c = ['LEA_NAME', 'LEANM', 'DISTRICT', 'DISTNAME']
    zip_c = ['LZIP', 'ZIP', 'MZIP']
    city_c = ['LCITY', 'CITY', 'MCITY']
    low_c = ['GSLO', 'LOGRADE', 'GRADE_LOW', 'LOW_GRADE']
    high_c = ['GSHI', 'HIGRADE', 'GRADE_HIGH', 'HIGH_GRADE']
    charter_c = ['CHARTER_TEXT', 'CHARTAUTH', 'CHARTER']
    magnet_c = ['MAGNET_TEXT', 'MAGNET']
    id_c = ['NCESSCH', 'NCES_ID']
    for feat in gj.get('features', []):
        p = feat.get('properties', feat.get('attributes', {}))
        z = str(pick(p, zip_c) or '')[:5]
        if z[:3] not in prefixes: continue
        geom = feat.get('geometry') or {}
        coords = geom.get('coordinates')
        if coords:
            lon, lat = coords[0], coords[1]
        else:
            lat, lon = pick(p, ['LAT', 'Y']), pick(p, ['LON', 'X'])
        try:
            lat, lon = float(lat), float(lon)
        except Exception:
            continue
        magnet = str(pick(p, magnet_c) or '').strip().upper() in ('Y', 'YES', '1', 'TRUE')
        charter = str(pick(p, charter_c) or '').strip().upper() not in ('', 'N', 'NO', '0', 'FALSE', 'NONE', 'NOT APPLICABLE')
        typ = 'magnet' if magnet else ('charter' if charter else 'public')
        low, high = pick(p, low_c), pick(p, high_c)
        out.append({
            'name': pick(p, name_c), 'district': pick(p, dist_c), 'level': level(low, high),
            'type': typ, 'lat': lat, 'lon': lon, 'city': pick(p, city_c), 'zip': z,
            'grades': grades_label(low, high), 'rating': None, 'rating_year': None,
            'tea_id': re.sub(r'\D', '', str(pick(p, id_c) or ''))[-9:].zfill(9) if pick(p, id_c) else None,
            'url': None,
        })
    print(f'  {len(out)} public/charter campuses from NCES in area')
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ratings', required=True, help='TEA campus accountability CSV (Report Level: Campus)')
    ap.add_argument('--private', help='NCES Private School Survey (PSS) school-level CSV')
    a = ap.parse_args()
    zips, prefixes = load_zips()

    ratings = {}
    for r in read_csv_flex(a.ratings):
        cid = re.sub(r'\D', '', pick(r, COLS['rating']['id']) or '')
        rt = (pick(r, COLS['rating']['rating']) or '').strip().upper()[:1]
        yr = pick(r, COLS['rating']['year'])
        if cid and rt in 'ABCDF':
            ratings[cid.zfill(9)] = (rt, yr)
    print(f'{len(ratings)} campus ratings loaded')

    schools = fetch_nces_public(zips, prefixes)
    matched = 0
    for s in schools:
        if s['tea_id'] and s['tea_id'] in ratings:
            s['rating'], s['rating_year'] = ratings[s['tea_id']]
            matched += 1
    print(f'{matched} campuses matched to a TEA rating')

    if a.private:
        n = 0
        for r in read_csv_flex(a.private):
            z = (pick(r, COLS['private']['zip']) or '')[:5]
            if z[:3] not in prefixes: continue
            lat, lon = pick(r, COLS['private']['lat']), pick(r, COLS['private']['lon'])
            try: lat, lon = float(lat), float(lon)
            except Exception: continue
            low, high = pick(r, COLS['private']['low']), pick(r, COLS['private']['high'])
            schools.append({'name': pick(r, COLS['private']['name']), 'district': 'Private', 'level': level(low, high),
                            'type': 'private', 'lat': lat, 'lon': lon, 'city': pick(r, COLS['private']['city']), 'zip': z,
                            'grades': grades_label(low, high), 'rating': None, 'rating_year': None, 'tea_id': None, 'url': None})
            n += 1
        print(f'{n} private schools in area')

    out = {'asof': datetime.date.today().isoformat(),
           'source': 'NCES EDGE (locations) + TEA Accountability (ratings)' + (' + NCES PSS (private)' if a.private else ''),
           'districts': {}, 'schools': schools}
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, 'schools.json'), 'w'))
    print(f'wrote data/schools.json: {len(schools)} schools total')

if __name__ == '__main__':
    main()
