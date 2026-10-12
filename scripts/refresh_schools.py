#!/usr/bin/env python3
"""Build data/schools.json for the DFW Neighborhood Atlas.

Sources (all free, no keys):
1. Public + charter school LOCATIONS, district, grade span, charter flag: NCES EDGE Open Data, the "ADMINDATA"
   public-school layer (it carries district name, TEA district number, grade span and lat/lon -- the older
   GEOCODE layer used before v15.1 had none of those, which is why every school lost its district and grade level).
   https://nces.ed.gov/opengis/rest/services/K12_School_Locations/
   Display names come from the companion GEOCODE layer (properly cased: "Ben Milam Elementary School").
2. A-F RATINGS: the TEA campus accountability CSV you download once a year (data-sources/tea_ratings.csv; Report
   Level: Campus). TEA campus numbers are NOT NCES school IDs, so a rating is matched by TEA DISTRICT number
   (NCES's ST_LEAID, e.g. "TX-057905") plus the campus NAME inside that district (names come from the same TEA
   files, so most match exactly; the rest by close spelling).
3. Private schools: NCES Private School Survey CSV (data-sources/pss.csv), optional. Texas does not rate them.

Only schools within SCHOOL_RADIUS_MI of some neighborhood on the map are kept (the page lists the closest 12 by
level within 6 miles; 4 keeps the file small and the lists full).

Usage:
  python scripts/refresh_schools.py --ratings data-sources/tea_ratings.csv --private data-sources/pss.csv
  (test hook) --nces-json file.json   a JSON list of ADMINDATA attribute dicts, used instead of the network
"""
import argparse, csv, difflib, json, math, os, re, sys, datetime, time, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')

NCES_BASE = 'https://nces.ed.gov/opengis/rest/services/K12_School_Locations'
# newest first; NCES publishes a new school-year layer each year, so try the next few years before older ones
LAYER_YEARS = ['2627', '2526', '2425', '2324']
SCHOOL_RADIUS_MI = 4.0
# NCES STATUS codes worth showing: 1 open, 3 new, 4 added, 5 changed boundary, 8 reopened (2 closed, 6 inactive, 7 future)
OK_STATUS = {'1', '3', '4', '5', '8'}
# keep ordinary campuses; leave out alternative-ed (DAEP/JJAEP), special-ed-only and similar non-neighborhood campuses
OK_TYPES = {'regular school', 'career and technical school'}

COLS = {
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


def annotate(level, msg):
    """GitHub Actions annotation: shown on the run's summary page, readable without opening the step log."""
    print(f"::{level}::" + str(msg).replace('%', '%25').replace('\r', ' ').replace('\n', ' ')[:900], flush=True)


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


def load_atlas():
    a = json.load(open(os.path.join(DATA, 'atlas.json')))
    zips, pts = set(), []
    for area in a['areas'].values():
        for h in area['neighborhoods']:
            zips.update(h['zips'])
            if h.get('lat') is not None and h.get('lon') is not None:
                pts.append((float(h['lat']), float(h['lon'])))
    return zips, pts


def miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def near_any(lat, lon, pts, limit):
    for la, lo in pts:
        if abs(la - lat) > 0.1 or abs(lo - lon) > 0.12: continue   # cheap pre-filter (~7 mi)
        if miles(lat, lon, la, lo) <= limit: return True
    return False


# ---------------------------------------------------------------- names
ACRONYMS = {'ISD', 'CISD', 'MSD', 'CSD', 'USD', 'KIPP', 'IDEA', 'STEM', 'PK', 'AEP', 'DAEP', 'JJAEP', 'II', 'III', 'IV',
            'VI', 'VII', 'VIII', 'IX', 'XI', 'YES', 'ECC', 'CTE', 'TX', 'USA', 'ABC', 'LLC', 'ESC', 'LEAP', 'BASIS'}
SMALL = {'of', 'the', 'and', 'at', 'for', 'in', 'on', 'to', 'a', 'an'}
ABBR = {'EL': 'Elementary', 'ELEM': 'Elementary', 'INT': 'Intermediate', 'MS': 'Middle School', 'HS': 'High School',
        'ACAD': 'Academy', 'SCH': 'School', 'CTR': 'Center', 'SR': 'Senior', 'JR': 'Junior', 'INTL': 'International',
        'ST': 'St.', 'MT': 'Mt.', 'DIV': 'Division'}


def pretty(name):
    """'EWELL D WALKER MIDDLE' -> 'Ewell D. Walker Middle'; 'HIGHLAND PARK H S' -> 'Highland Park High School'."""
    s = re.sub(r'\bH S\b', 'HS', str(name or '').strip().upper())
    s = re.sub(r'\bJ H\b', 'JH', s)
    out = []
    for i, w in enumerate(re.split(r'\s+', s)):
        if not w: continue
        if w in ABBR: out.append(ABBR[w]); continue
        if w == 'JH': out.append('Junior High'); continue
        if w in ACRONYMS: out.append(w); continue
        if len(w) == 1 and w.isalpha(): out.append(w + '.'); continue
        t = '-'.join(p[:1] + p[1:].lower() for p in w.split('-'))
        t = re.sub(r"\bMc([a-z])", lambda m: 'Mc' + m.group(1).upper(), t)
        t = re.sub(r"'([a-z])", lambda m: "'" + m.group(1), t)
        out.append(t.lower() if (i and t.lower() in SMALL) else t)
    return ' '.join(out)


def nkey(name):
    """Comparable form of a campus name: TEA and NCES spell the same school differently (EL / ELEMENTARY, H S / HIGH SCHOOL...)."""
    s = re.sub(r'[^A-Z0-9 ]', ' ', str(name or '').upper())
    s = re.sub(r'\b(ELEMENTARY|ELEM)\b', 'EL', s)
    s = re.sub(r'\b(HIGH SCHOOL|H S|HIGH)\b', 'HS', s)
    s = re.sub(r'\b(MIDDLE SCHOOL|MIDDLE)\b', 'MS', s)
    s = re.sub(r'\b(JUNIOR HIGH SCHOOL|JUNIOR HIGH|JR HIGH|J H)\b', 'JH', s)
    s = re.sub(r'\b(INTERMEDIATE|INT)\b', 'INT', s)
    s = re.sub(r'\b(ACADEMY)\b', 'ACAD', s)
    s = re.sub(r'\b(INTERNATIONAL)\b', 'INTL', s)
    s = re.sub(r'\b(SCHOOL|SCH|AND|THE|OF)\b', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


# ---------------------------------------------------------------- NCES
def http_json(url, tries=3, timeout=120):
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'dfw-atlas-builder/1.0', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            last = e; time.sleep(2 * (a + 1))
    raise RuntimeError(f'{type(last).__name__}: {last}')


def arcgis_pages(layer_url, where, fields, page=1000, max_pages=40):
    """Every record of a layer matching `where`, paged (ArcGIS caps one response at 1,000-2,000 rows)."""
    out = []
    for p in range(max_pages):
        q = {'where': where, 'outFields': fields, 'returnGeometry': 'false', 'f': 'json',
             'orderByFields': 'OBJECTID', 'resultOffset': p * page, 'resultRecordCount': page}
        j = http_json(layer_url + '/query?' + urllib.parse.urlencode(q))
        if 'error' in j:
            raise RuntimeError(f"layer error: {j['error']}")
        rows = [f['attributes'] for f in j.get('features', [])]
        out.extend(rows)
        if len(rows) < page and not j.get('exceededTransferLimit'): break
    return out


def fetch_nces_admin():
    """All Texas public/charter campuses from the newest NCES ADMINDATA layer that answers. Returns (rows, year)."""
    fields = 'NCESSCH,SCH_NAME,LEA_NAME,ST_LEAID,LCITY,LZIP,GSLO,GSHI,CHARTER_TEXT,SCHOOL_TYPE_TEXT,STATUS,VIRTUAL,LATCOD,LONCOD'
    errs = []
    for yy in LAYER_YEARS:
        url = f'{NCES_BASE}/EDGE_ADMINDATA_PUBLICSCH_{yy}/MapServer/1'
        try:
            rows = arcgis_pages(url, "LSTATE LIKE 'TX%'", fields)   # NCES pads the state to 'TX ' -- an equals test finds nothing
        except Exception as e:
            errs.append(f'{yy}: {e}'); continue
        if rows:
            print(f'  NCES ADMINDATA {yy}: {len(rows)} Texas campuses')
            return rows, yy
        errs.append(f'{yy}: no rows')
    print('  NCES fetch failed -- ' + ' | '.join(errs))
    return [], None


def fetch_nces_names(year):
    """NCESSCH -> properly cased campus name, from the GEOCODE layer of the same year (best effort)."""
    try:
        rows = arcgis_pages(f'{NCES_BASE}/EDGE_GEOCODE_PUBLICSCH_{year}/MapServer/0', "STATE='TX'", 'NCESSCH,NAME')
        return {r['NCESSCH']: r['NAME'] for r in rows if r.get('NCESSCH') and r.get('NAME')}
    except Exception as e:
        print(f'  (display names: GEOCODE layer unavailable, using tidied admin names: {e})')
        return {}


# ---------------------------------------------------------------- TEA ratings
def load_tea(path):
    """-> (by_district {district6: [(nkey, rating, campus9, name)]}, year). Skips the file's second header row of
    field codes, and campuses without a letter grade ('Not Rated')."""
    rows = read_csv_flex(path)
    by_dist, year, n = {}, None, 0
    for r in rows:
        cid = re.sub(r'\D', '', str(pick(r, ['Campus #', 'CAMPUS', 'Campus Number', 'CAMPUS_ID']) or ''))
        if len(cid) < 7: continue
        if year is None:
            for k in r:
                m = re.match(r'\s*(\d{4}) Overall Rating', k or '')
                if m: year = m.group(1)
        rt = str(pick(r, ['2026 Overall Rating', '2025 Overall Rating', '2024 Overall Rating', 'C_RATING', 'Rating',
                          'Overall Rating', 'RATING']) or '').strip().upper()
        if rt not in ('A', 'B', 'C', 'D', 'F'): continue
        cid = cid.zfill(9)
        dist = re.sub(r'\D', '', str(pick(r, ['District #', 'DISTRICT']) or cid[:6])).zfill(6)
        name = pick(r, ['Campus Name', 'CAMPNAME']) or ''
        by_dist.setdefault(dist, []).append((nkey(name), rt, cid, name))
        n += 1
    print(f'{n} rated campuses loaded from {os.path.basename(path)} (rating year {year})')
    return by_dist, year


def match_ratings(schools, by_dist):
    """Attach rating/tea_id to public schools by (TEA district number, campus name). Greedy best match first so two
    NCES campuses never claim the same TEA campus. Returns number matched."""
    cands = []
    for i, s in enumerate(schools):
        pool = by_dist.get(s.get('_dist') or '', [])
        if not pool: continue
        k = nkey(s['_admin_name'])
        for j, (tk, rt, cid, tname) in enumerate(pool):
            ratio = 1.0 if tk == k else difflib.SequenceMatcher(None, k, tk).ratio()
            if ratio >= 0.86: cands.append((ratio, i, s['_dist'], j))
    cands.sort(key=lambda t: -t[0])
    used_s, used_t, n = set(), set(), 0
    for ratio, i, d, j in cands:
        if i in used_s or (d, j) in used_t: continue
        used_s.add(i); used_t.add((d, j))
        _, rt, cid, _ = by_dist[d][j]
        schools[i]['rating'], schools[i]['tea_id'] = rt, cid
        n += 1
    return n


# ---------------------------------------------------------------- build
def build_public(rows, names, zips, pts):
    out = []
    for p in rows:
        if str(p.get('STATUS') or '').strip() not in OK_STATUS: continue
        if str(p.get('SCHOOL_TYPE_TEXT') or '').strip().lower() not in OK_TYPES: continue
        if str(p.get('VIRTUAL') or '').strip().upper() in ('A', 'FV'): continue   # fully virtual: no neighborhood
        z = str(p.get('LZIP') or '')[:5]
        try: lat, lon = float(p['LATCOD']), float(p['LONCOD'])
        except Exception: continue
        if z not in zips and not near_any(lat, lon, pts, SCHOOL_RADIUS_MI): continue
        low, high = p.get('GSLO'), p.get('GSHI')
        admin_name = str(p.get('SCH_NAME') or '').strip()
        charter = str(p.get('CHARTER_TEXT') or '').strip().upper() == 'YES'
        out.append({
            'name': names.get(p.get('NCESSCH')) or pretty(admin_name),
            'district': pretty(p.get('LEA_NAME')) or None,
            'level': level(low, high), 'type': 'charter' if charter else 'public',
            'lat': round(lat, 5), 'lon': round(lon, 5), 'city': (str(p.get('LCITY') or '').strip().title() or None), 'zip': z,
            'grades': grades_label(low, high), 'rating': None, 'rating_year': None, 'tea_id': None, 'url': None,
            '_admin_name': admin_name, '_dist': re.sub(r'\D', '', str(p.get('ST_LEAID') or '')).zfill(6)
                         if p.get('ST_LEAID') else None,
        })
    return out


def carry_magnets(schools, path):
    """NCES's current layers carry no magnet flag. Keep the ones already marked in the previous schools.json: a
    previous magnet within 0.35 mi of a new campus with a similar name stays a magnet."""
    try: prev = json.load(open(path))['schools']
    except Exception: return 0
    mags = [s for s in prev if s.get('type') == 'magnet']
    n = 0
    for m in mags:
        best, br = None, 0.0
        for s in schools:
            if s['type'] != 'public' or abs(s['lat'] - m['lat']) > 0.01 or abs(s['lon'] - m['lon']) > 0.01: continue
            if miles(s['lat'], s['lon'], m['lat'], m['lon']) > 0.35: continue
            r = difflib.SequenceMatcher(None, nkey(s['name']), nkey(m['name'])).ratio()
            if r > br: best, br = s, r
        if best is not None and br >= 0.5:
            best['type'] = 'magnet'; n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ratings', required=True, help='TEA campus accountability CSV (Report Level: Campus)')
    ap.add_argument('--private', help='NCES Private School Survey (PSS) school-level CSV')
    ap.add_argument('--nces-json', help='(testing) JSON list of ADMINDATA attribute dicts to use instead of the network')
    a = ap.parse_args()
    zips, pts = load_atlas()

    if a.nces_json:
        rows, names = json.load(open(a.nces_json)), {}
    else:
        rows, year = fetch_nces_admin()
        names = fetch_nces_names(year) if rows else {}
    schools = build_public(rows, names, zips, pts)
    print(f'  {len(schools)} public/charter campuses kept near the map')

    by_dist, tea_year = load_tea(a.ratings)
    matched = match_ratings(schools, by_dist)
    for s in schools:
        if s['rating']: s['rating_year'] = tea_year
    n_mag = carry_magnets(schools, os.path.join(DATA, 'schools.json'))
    in_rated_district = sum(1 for s in schools if s['_dist'] in by_dist)
    print(f'{matched} campuses matched to a TEA rating ({n_mag} previous magnet flags carried over)')
    for s in schools:
        s.pop('_admin_name', None); s.pop('_dist', None)

    if a.private:
        n = 0
        for r in read_csv_flex(a.private):
            z = (pick(r, COLS['private']['zip']) or '')[:5]
            if z not in zips: continue
            lat, lon = pick(r, COLS['private']['lat']), pick(r, COLS['private']['lon'])
            try: lat, lon = float(lat), float(lon)
            except Exception: continue
            low, high = pick(r, COLS['private']['low']), pick(r, COLS['private']['high'])
            schools.append({'name': pretty(pick(r, COLS['private']['name'])) if str(pick(r, COLS['private']['name']) or '').isupper() else pick(r, COLS['private']['name']),
                            'district': 'Private', 'level': level(low, high),
                            'type': 'private', 'lat': round(lat, 5), 'lon': round(lon, 5), 'city': (pick(r, COLS['private']['city']) or '').title() or None, 'zip': z,
                            'grades': grades_label(low, high), 'rating': None, 'rating_year': None, 'tea_id': None, 'url': None})
            n += 1
        print(f'{n} private schools in the map\'s zip codes')

    # Never replace a working schools.json with a broken one: stop WITHOUT writing if the public-school pull failed
    # or came back without the district names / grade spans the page's Schools tab depends on. The committed file
    # stays as it was and this step shows red in the Actions run (which sends the failure email).
    pub = [s for s in schools if s['type'] != 'private']
    no_district = sum(1 for s in pub if not s.get('district'))
    all_k12 = bool(pub) and all(s.get('level') == 'K12' for s in pub)
    if not pub or no_district > 0.2 * len(pub) or all_k12:
        msg = (f'REFUSING TO WRITE data/schools.json: {len(pub)} public campuses, {no_district} without a district name, '
               f'all-K12={all_k12}. The NCES layer is unreachable or no longer carries district / grade-span fields. '
               f'The existing file was left untouched.')
        print(msg); annotate('error', msg)
        sys.exit(1)

    pct = round(100 * matched / max(1, len(pub)))
    lv = {k: sum(1 for s in pub if s['level'] == k) for k in ('ES', 'MS', 'HS', 'K12')}
    annotate('notice', f'Schools: {len(pub)} public/charter ({lv}), {len(schools) - len(pub)} private; {matched} rated ({pct}%), '
                       f'{in_rated_district} in districts with TEA ratings')
    if pub and matched < 0.4 * in_rated_district:
        annotate('warning', f'Only {matched} of {in_rated_district} campuses in rated districts matched a TEA rating -- the campus-name join may need attention.')

    out = {'asof': datetime.date.today().isoformat(),
           'source': 'NCES EDGE (locations, districts) + TEA Accountability (ratings)' + (' + NCES PSS (private)' if a.private else ''),
           'districts': {}, 'schools': schools}
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, 'schools.json'), 'w'), separators=(',', ':'))
    print(f'wrote data/schools.json: {len(schools)} schools total')


if __name__ == '__main__':
    main()
