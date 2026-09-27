#!/usr/bin/env python3
"""Build data/hmda_stats.json for Michael's INTERNAL lending-signal page (site/internal-lending/) --
never used on the public map.

Source: the CFPB/FFIEC HMDA Data Browser Aggregation API (free, no key):
  https://ffiec.cfpb.gov/documentation/api/data-browser/
For each of the atlas's 5 counties, pulls total originated-loan counts and dollar volume for the
year, split two ways: by loan purpose (home purchase / refinance / cash-out refinance), and
separately by construction method (site-built / manufactured home). This is aggregate market
activity only -- the same numbers anyone can look up on the CFPB's own public data browser website;
nothing here is lender- or borrower-identifying, and it deliberately never requests HMDA's race,
ethnicity or sex breakdowns (fair-lending: this stays a volume/geography signal, not anything that
could read as targeting by protected class).

Known limit of this API: it 400s if you combine more than 2 filter/group-by parameters in one call
(confirmed by trial), so this makes 2 separate calls per county rather than 1.

What this does NOT yet do (a natural next step, not built here): break results down by individual
lender. HMDA's raw records only carry each lender's LEI, not a readable name -- that needs a second
lookup (e.g. against the GLEIF LEI registry) to turn "5493001KJTIIGC8Y1R12" into a lender's actual
name, which is more plumbing than this first pass covers.

Usage: python scripts/build_hmda.py [--year 2024]
"""
import argparse, json, os, urllib.request, urllib.parse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# Deliberately NOT site/data/ (the public map's data folder) -- this stays under the internal page's
# own folder. Note this is organizational separation, not real access control: GitHub Pages is a
# static host, so anyone who knows or guesses this exact path can still fetch the file directly. See
# the caveat in site/internal-lending/README.md.
DATA = os.path.join(HERE, '..', 'internal-lending', 'data')
API = 'https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations'
COUNTIES = {'48113': 'Dallas', '48085': 'Collin', '48121': 'Denton', '48439': 'Tarrant', '48397': 'Rockwall'}
PURPOSE_NAMES = {'1': 'Home purchase', '31': 'Refinance', '32': 'Cash-out refinance'}
CONSTRUCTION_NAMES = {'1': 'Site-built', '2': 'Manufactured home'}


def query(year, county, **params):
    p = {'years': year, 'counties': county}
    p.update(params)
    url = API + '?' + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={'User-Agent': 'dfw-atlas-builder/1.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def try_year(year, county):
    """Returns (purpose_rows, construction_rows) or None if this year has no data for this county
    (used to auto-fall-back to the prior year if the requested one is too fresh to have filings)."""
    try:
        a = query(year, county, loan_purposes='1,31,32', actions_taken=1)
        b = query(year, county, construction_methods='1,2', actions_taken=1)
    except Exception as e:
        print(f'    {county} {year}: request failed: {e}')
        return None
    if not a.get('aggregations') and not b.get('aggregations'):
        return None
    return a.get('aggregations', []), b.get('aggregations', [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--year', type=int, help='HMDA data year (defaults to trying this year, then last year)')
    a = ap.parse_args()
    years_to_try = [a.year] if a.year else [datetime.date.today().year - 1, datetime.date.today().year - 2]

    counties_out = {}
    used_year = None
    for fips, name in COUNTIES.items():
        print(f'{name} County ({fips})...')
        result, year = None, None
        for y in years_to_try:
            result = try_year(y, fips)
            if result:
                year = y
                break
        if not result:
            print(f'  no data for any of {years_to_try}; skipping')
            continue
        used_year = used_year or year
        purpose_rows, construction_rows = result
        out = {'name': name, 'year': year}
        for row in purpose_rows:
            lp = str(row.get('loan_purposes'))
            label = PURPOSE_NAMES.get(lp)
            if label:
                out[label] = {'count': row['count'], 'sum': row['sum']}
        for row in construction_rows:
            cm = str(row.get('construction_methods'))
            label = CONSTRUCTION_NAMES.get(cm)
            if label:
                out[label] = {'count': row['count'], 'sum': row['sum']}
        counties_out[fips] = out
        print(f'  {name}: ' + ', '.join(f"{k}={v['count']}" for k, v in out.items() if isinstance(v, dict)))

    out = {
        'asof': datetime.date.today().isoformat(),
        'year': used_year,
        'source': 'CFPB/FFIEC HMDA Data Browser (aggregate, no borrower- or lender-identifying data)',
        'note': 'Originated loans only (actions_taken=1). "Site-built" vs "Manufactured home" is HMDA\'s '
                'construction-method field, not a "new construction loan" flag -- HMDA does not have a clean '
                'new-construction category separate from Home purchase.',
        'counties': counties_out,
    }
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(os.path.join(DATA, 'hmda_stats.json'), 'w'), indent=None)
    print(f'wrote data/hmda_stats.json: {len(counties_out)} counties, year {used_year}')


if __name__ == '__main__':
    main()
