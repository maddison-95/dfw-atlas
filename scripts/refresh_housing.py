#!/usr/bin/env python3
"""Build data/housing.json for the DFW Neighborhood Atlas.

Sources (all free):
  * Zillow Research ZHVI (typical home value) and ZORI (typical rent), zip and neighborhood level, monthly CSVs.
  * U.S. Census ACS 5-year profile tables by ZCTA (needs a free key: https://api.census.gov/data/key_signup.html).
  * Optional: Redfin Data Center zip-code market tracker (median sale price, days on market, homes sold).
      Download zip_code_market_tracker.tsv000.gz from https://www.redfin.com/news/data-center/ and pass --redfin PATH.

Usage:
  python scripts/refresh_housing.py --census-key YOUR_KEY [--redfin zip_code_market_tracker.tsv000.gz]
The script reads the atlas neighborhood list (data/atlas.json, written by assemble.py) to know which zips and
neighborhood names to keep, and writes data/housing.json.
"""
import argparse, csv, gzip, io, json, os, sys, urllib.request, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')

ZILLOW = {
    'zhvi_zip': 'https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv',
    'zhvi_hood': 'https://files.zillowstatic.com/research/public_csvs/zhvi/Neighborhood_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv',
    'zori_zip': 'https://files.zillowstatic.com/research/public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_month.csv',
}
ACS_VARS = {  # ACS 5-year data profile variables
    'DP05_0001E': 'pop', 'DP03_0062E': 'income', 'DP05_0018E': 'age',
    'DP04_0046PE': 'owner_pct', 'DP02_0016E': 'hh_size', 'DP02_0068PE': 'bach_pct',
}

def fetch(url):
    print('  GET', url)
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()

def load_atlas():
    p = os.path.join(DATA, 'atlas.json')
    a = json.load(open(p))
    zips, names = set(), set()
    for area in a['areas'].values():
        for h in area['neighborhoods']:
            zips.update(h['zips']); names.add(h['name'])
    return zips, names

def month_cols(header):
    return [c for c in header if len(c) == 10 and c[4] == '-' and c[7] == '-']

def series_from_row(row, cols):
    out = []
    for c in cols:
        v = row.get(c, '')
        if v not in ('', None):
            out.append([c[:7], float(v)])
    return out

def pct_change(series, months):
    if len(series) <= months: return None
    a, b = series[-1 - months][1], series[-1][1]
    return (b / a - 1) * 100 if a else None

def zillow(zips, names):
    out_zip, out_hood, asof = {}, {}, None
    # ZHVI by zip
    rows = list(csv.DictReader(io.StringIO(fetch(ZILLOW['zhvi_zip']).decode('utf-8'))))
    cols = month_cols(rows[0].keys()); asof = cols[-1][:7]
    for r in rows:
        z = r['RegionName'].zfill(5)
        if z in zips:
            s = series_from_row(r, cols)
            if not s: continue
            out_zip[z] = {'zhvi': s[-1][1], 'zhvi_1y': pct_change(s, 12), 'zhvi_5y': pct_change(s, 60), 'series': s[-60:]}
    # ZORI by zip
    rows = list(csv.DictReader(io.StringIO(fetch(ZILLOW['zori_zip']).decode('utf-8'))))
    cols = month_cols(rows[0].keys())
    for r in rows:
        z = r['RegionName'].zfill(5)
        if z in zips:
            s = series_from_row(r, cols)
            if s:
                out_zip.setdefault(z, {}).update({'zori': s[-1][1], 'zori_1y': pct_change(s, 12)})
    # ZHVI by neighborhood (Dallas metro only)
    rows = list(csv.DictReader(io.StringIO(fetch(ZILLOW['zhvi_hood']).decode('utf-8'))))
    cols = month_cols(rows[0].keys())
    for r in rows:
        if r.get('State') != 'TX' or 'Dallas' not in (r.get('Metro') or ''): continue
        n = r['RegionName']
        if n in names:
            s = series_from_row(r, cols)
            if s:
                out_hood[n] = {'zhvi': s[-1][1], 'zhvi_1y': pct_change(s, 12), 'zhvi_5y': pct_change(s, 60), 'series': s[-60:], 'city': r.get('City')}
    return out_zip, out_hood, asof

def census(zips, key):
    get = ','.join(ACS_VARS)
    year = datetime.date.today().year - 2   # ACS 5-year usually lags ~2 years
    for y in (year, year - 1):
        url = f'https://api.census.gov/data/{y}/acs/acs5/profile?get={get}&for=zip%20code%20tabulation%20area:*&key={key}'
        try:
            data = json.loads(fetch(url)); break
        except Exception as e:
            print('  ACS', y, 'failed:', e); data = None
    if not data: return {}, None
    hdr = data[0]; out = {}
    for row in data[1:]:
        rec = dict(zip(hdr, row)); z = rec.get('zip code tabulation area')
        if z in zips:
            o = {}
            for var, k in ACS_VARS.items():
                try:
                    v = float(rec[var]); o[k] = None if v < -100000 else v
                except Exception: o[k] = None
            o['acs_year'] = f'ACS {y-4}-{y}'
            out[z] = o
    return out, y

def redfin(path, zips):
    out = {}
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8', errors='ignore') as f:
        rd = csv.DictReader(f, delimiter='\t')
        latest = {}
        for r in rd:
            if r.get('PROPERTY_TYPE') != 'All Residential': continue
            z = (r.get('REGION') or '').replace('Zip Code: ', '').strip()
            if z not in zips: continue
            per = r.get('PERIOD_END')
            if z not in latest or per > latest[z]['PERIOD_END']:
                latest[z] = r
        for z, r in latest.items():
            def num(k):
                try: return float(r[k])
                except Exception: return None
            out[z] = {'median_sale': num('MEDIAN_SALE_PRICE'), 'dom': num('MEDIAN_DOM'), 'homes_sold': num('HOMES_SOLD'),
                      'sale_asof': r['PERIOD_END'], 'sale_source': 'Redfin'}
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--census-key', default=os.environ.get('CENSUS_KEY'))
    ap.add_argument('--redfin', help='path to Redfin zip_code_market_tracker.tsv000.gz')
    a = ap.parse_args()
    zips, names = load_atlas()
    print(f'{len(zips)} zips, {len(names)} neighborhood names from atlas')
    print('Zillow...'); zz, zh, asof = zillow(zips, names)
    sources = ['Zillow Research ZHVI/ZORI']
    if a.census_key:
        print('Census...'); cz, y = census(zips, a.census_key)
        for z, o in cz.items(): zz.setdefault(z, {}).update(o)
        sources.append('U.S. Census ACS 5-year')
    else:
        print('  (no --census-key; skipping demographics)')
    if a.redfin:
        print('Redfin...'); rz = redfin(a.redfin, zips)
        for z, o in rz.items(): zz.setdefault(z, {}).update(o)
        sources.append('Redfin Data Center')
    out = {'asof': asof, 'generated': datetime.date.today().isoformat(), 'sources': sources, 'zips': zz, 'neighborhoods': zh}
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, 'housing.json'), 'w'))
    print(f'wrote data/housing.json: {len(zz)} zips, {len(zh)} neighborhood series, as of {asof}')

if __name__ == '__main__':
    main()
