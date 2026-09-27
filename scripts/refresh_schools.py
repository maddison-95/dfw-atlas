#!/usr/bin/env python3
"""Build data/schools.json for the DFW Neighborhood Atlas from three free downloads.

1. TEA campus directory with locations (public + charter, every campus in Texas):
   TEA "School and District File with Site Address" / AskTED download, or the TEA campus GIS layer exported to CSV.
   Needed columns (names vary by export; edit COLS below): campus name, district name, campus number (9-digit),
   grade span (low/high), latitude, longitude, city, zip, magnet flag (Y/N), charter flag.
2. TEA A–F accountability ratings (campus level): download the campus ratings CSV/XLSX from TXschools.gov or
   tea.texas.gov "Accountability Rating System" > data download. Needed columns: campus number, rating (A-F), year.
3. NCES Private School Survey (PSS) school-level CSV (https://nces.ed.gov/surveys/pss/pssdata.asp): private schools
   with name, city, zip, grades, latitude, longitude.

Usage:
  python scripts/refresh_schools.py --campuses tea_campuses.csv --ratings tea_ratings.csv --private pss.csv
Only campuses whose zip is in the atlas zip list (plus a small buffer of neighboring zips) are kept.
Starter entries in data/schools.json are replaced by the official records; ratings for private schools stay blank
because TEA does not rate them.
"""
import argparse, csv, json, os, re, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')

# Column-name candidates for each field (first match wins, case-insensitive). Edit to match your download.
COLS = {
    'campus': {
        'name': ['CAMPNAME', 'Campus Name', 'School Name', 'CAMPUS_NAME', 'NAME'],
        'district': ['DISTNAME', 'District Name', 'DISTRICT_NAME', 'District'],
        'id': ['CAMPUS', 'Campus Number', 'CAMPUS_ID', 'CampusNumber', 'Campus'],
        'low': ['GRDSPAN_LOW', 'Grade Range Low', 'LOW_GRADE', 'Low Grade', 'GRADE_LOW'],
        'high': ['GRDSPAN_HIGH', 'Grade Range High', 'HIGH_GRADE', 'High Grade', 'GRADE_HIGH'],
        'lat': ['LATITUDE', 'Latitude', 'Y', 'lat'],
        'lon': ['LONGITUDE', 'Longitude', 'X', 'lon'],
        'city': ['CITY', 'City', 'Campus City'],
        'zip': ['ZIP', 'Zip', 'Campus Zip', 'ZIP5'],
        'magnet': ['MAGNET', 'Magnet', 'Magnet Status', 'MAGNET_FLAG'],
        'charter': ['CHARTER', 'Charter Type', 'CHARTER_FLAG', 'Charter'],
        'website': ['WEBSITE', 'Web Address', 'URL', 'Campus Website'],
    },
    'rating': {
        'id': ['CAMPUS', 'Campus Number', 'CAMPUS_ID', 'Campus'],
        'rating': ['C_RATING', 'Rating', 'Overall Rating', 'RATING', '2025 Rating', '2024 Rating'],
        'year': ['YEAR', 'Year', 'Rating Year'],
    },
    'private': {
        'name': ['PINST', 'School Name', 'NAME'],
        'city': ['PCITY', 'City'],
        'zip': ['PZIP', 'Zip'],
        'low': ['LOGR20', 'LOGR22', 'Low Grade', 'LOGR'],
        'high': ['HIGR20', 'HIGR22', 'High Grade', 'HIGR'],
        'lat': ['LATITUDE', 'LATITUDE20', 'LATITUDE22', 'Latitude'],
        'lon': ['LONGITUDE', 'LONGITUDE20', 'LONGITUDE22', 'Longitude'],
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
    g = str(g).strip().upper().replace('EE', 'PK').replace('KINDERGARTEN', 'K')
    g = {'PK': 'PK', 'PRE-K': 'PK', 'K': 'K', 'KG': 'K'}.get(g, g)
    if g.isdigit(): g = g.zfill(2)
    return g

def level(low, high):
    lo, hi = norm_grade(low), norm_grade(high)
    def idx(g):
        return GRADE_ORDER.index(g) if g in GRADE_ORDER else None
    a, b = idx(lo), idx(hi)
    if a is None or b is None: return 'K12'
    if b <= GRADE_ORDER.index('06'): return 'ES'
    if a >= GRADE_ORDER.index('06') and b <= GRADE_ORDER.index('09'): return 'MS'
    if a >= GRADE_ORDER.index('09'): return 'HS'
    if a <= GRADE_ORDER.index('05') and b <= GRADE_ORDER.index('08'): return 'ES'   # K-8 counts as elementary+middle -> ES
    return 'K12'

def grades_label(low, high):
    lo, hi = norm_grade(low), norm_grade(high)
    if not lo or not hi: return ''
    f = lambda g: g.lstrip('0') if g and g[0] == '0' else g
    return f'{f(lo)}-{f(hi)}'

def read(path):
    with open(path, newline='', encoding='utf-8', errors='ignore') as f:
        sample = f.read(4096); f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=',\t|')
        return list(csv.DictReader(f, dialect=dialect))

def load_zips():
    a = json.load(open(os.path.join(DATA, 'atlas.json')))
    zips = set()
    for area in a['areas'].values():
        for h in area['neighborhoods']: zips.update(h['zips'])
    # buffer: nearby zips share the first 3 digits
    prefixes = {z[:3] for z in zips}
    return zips, prefixes

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--campuses', required=True)
    ap.add_argument('--ratings', required=True)
    ap.add_argument('--private')
    a = ap.parse_args()
    zips, prefixes = load_zips()

    ratings = {}
    for r in read(a.ratings):
        cid = re.sub(r'\D', '', pick(r, COLS['rating']['id']) or '')
        rt = (pick(r, COLS['rating']['rating']) or '').strip().upper()[:1]
        yr = pick(r, COLS['rating']['year'])
        if cid and rt in 'ABCDF' and rt:
            ratings[cid.zfill(9)] = (rt, yr)
    print(f'{len(ratings)} campus ratings')

    schools, districts = [], {}
    for r in read(a.campuses):
        z = (pick(r, COLS['campus']['zip']) or '')[:5]
        if z[:3] not in prefixes: continue
        cid = re.sub(r'\D', '', pick(r, COLS['campus']['id']) or '').zfill(9)
        lat, lon = pick(r, COLS['campus']['lat']), pick(r, COLS['campus']['lon'])
        try: lat, lon = float(lat), float(lon)
        except Exception: continue
        magnet = (pick(r, COLS['campus']['magnet']) or '').strip().upper() in ('Y', 'YES', '1', 'TRUE')
        charter = (pick(r, COLS['campus']['charter']) or '').strip().upper() not in ('', 'N', 'NO', '0', 'FALSE', 'NONE')
        typ = 'magnet' if magnet else ('charter' if charter else 'public')
        rt = ratings.get(cid, (None, None))
        low, high = pick(r, COLS['campus']['low']), pick(r, COLS['campus']['high'])
        schools.append({'name': pick(r, COLS['campus']['name']), 'district': pick(r, COLS['campus']['district']),
                        'level': level(low, high), 'type': typ, 'lat': lat, 'lon': lon,
                        'city': pick(r, COLS['campus']['city']), 'zip': z, 'grades': grades_label(low, high),
                        'rating': rt[0], 'rating_year': rt[1], 'tea_id': cid, 'url': pick(r, COLS['campus']['website'])})
    print(f'{len(schools)} public/charter campuses in area')

    if a.private:
        n = 0
        for r in read(a.private):
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

    out = {'asof': datetime.date.today().isoformat(), 'source': 'TEA campus directory + A-F accountability; NCES Private School Survey',
           'districts': districts, 'schools': schools}
    json.dump(out, open(os.path.join(DATA, 'schools.json'), 'w'))
    print('wrote data/schools.json')

if __name__ == '__main__':
    main()
