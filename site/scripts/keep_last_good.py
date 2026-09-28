#!/usr/bin/env python3
"""Keep-last-good guard + archive for the data files the refresh workflows rewrite.

Run inside a GitHub Action after the refresh scripts and BEFORE the commit step. For each data file named:

  1. If the file was not changed by this run, nothing happens.
  2. If the new version is not valid JSON, or is drastically smaller than the committed version (fewer bytes
     than MIN_RATIO of the last one, or missing top-level keys the last one had), the new version is REJECTED:
     the committed version is restored from git and the rejection is printed as a workflow warning. The site
     keeps serving the last good data instead of a broken or half-empty file.
  3. If it passes, a dated copy is written to data/archive/<YYYY-MM>/<file> so any month can be rolled back by
     hand (copy it over the live file and commit), and a small data/archive/index.json lists what's there.
     Archives are pruned to the last KEEP_MONTHS months.

Why bytes and top-level keys rather than a per-file schema: the files have different shapes (housing.json is
keyed by neighborhood, schools.json is a list, lot_stats.json has two dicts), and what a partial upstream
outage does to all of them is the same -- the file gets much smaller or loses a section. A slightly smaller
file (a neighborhood dropped from Zillow's list, say) still passes; a 40% smaller one does not.

Usage:  python scripts/keep_last_good.py data/housing.json data/schools.json data/lot_stats.json
Exit code is 0 either way (rejection is a warning, not a failure, so the other files still get committed);
pass --strict to exit 1 when anything was rejected.
"""
import argparse, datetime, json, os, shutil, subprocess, sys

MIN_RATIO = 0.6       # new file must be at least this fraction of the committed file's size
KEEP_MONTHS = 36      # archives older than this are pruned
ARCHIVE = 'data/archive'


def git_show(path):
    r = subprocess.run(['git', 'show', f'HEAD:{path}'], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def changed(path):
    r = subprocess.run(['git', 'status', '--porcelain', '--', path], capture_output=True, text=True)
    return bool(r.stdout.strip())


def top_keys(obj):
    if isinstance(obj, dict):
        return set(obj.keys())
    return set()


def check(path):
    """Return (ok, reason)."""
    try:
        new_raw = open(path, 'rb').read()
        new = json.loads(new_raw)
    except Exception as e:
        return False, f'not valid JSON ({e})'
    old_raw = git_show(path)
    if old_raw is None:
        return True, 'new file (nothing committed yet)'
    try:
        old = json.loads(old_raw)
    except Exception:
        return True, 'committed version was not valid JSON; accepting the new one'
    if len(old_raw) > 2000 and len(new_raw) < MIN_RATIO * len(old_raw):
        return False, f'shrank to {len(new_raw):,} bytes from {len(old_raw):,} ({100*len(new_raw)/len(old_raw):.0f}%)'
    missing = top_keys(old) - top_keys(new)
    # ignore date-ish/meta keys that legitimately come and go
    missing = {k for k in missing if not k.startswith('_') and k not in ('asof', 'generated', 'note', 'source')}
    if missing and len(top_keys(old)) <= 20:      # only meaningful when the top level is a handful of sections
        return False, f'missing top-level section(s): {", ".join(sorted(missing))}'
    if isinstance(old, dict) and isinstance(new, dict) and len(top_keys(old)) > 20:
        # keyed-by-record files (one key per neighborhood, say): don't lose more than 40% of the records
        if len(new) < MIN_RATIO * len(old):
            return False, f'record count fell to {len(new):,} from {len(old):,}'
    if isinstance(old, list) and isinstance(new, list) and len(old) > 20 and len(new) < MIN_RATIO * len(old):
        return False, f'list length fell to {len(new):,} from {len(old):,}'
    return True, f'{len(new_raw):,} bytes (was {len(old_raw):,})'


def restore(path):
    subprocess.run(['git', 'checkout', '--', path], check=False)


def archive(path, month):
    d = os.path.join(ARCHIVE, month); os.makedirs(d, exist_ok=True)
    shutil.copyfile(path, os.path.join(d, os.path.basename(path)))


def prune_and_index():
    if not os.path.isdir(ARCHIVE):
        return
    months = sorted(m for m in os.listdir(ARCHIVE) if os.path.isdir(os.path.join(ARCHIVE, m)) and len(m) == 7)
    for m in months[:-KEEP_MONTHS]:
        shutil.rmtree(os.path.join(ARCHIVE, m), ignore_errors=True)
    months = months[-KEEP_MONTHS:]
    index = {m: sorted(os.listdir(os.path.join(ARCHIVE, m))) for m in months}
    json.dump({'note': 'Dated copies of the live data files, one folder per month, written by scripts/keep_last_good.py after each accepted refresh. To roll back: copy a file from here over data/<file> and commit.',
               'months': index}, open(os.path.join(ARCHIVE, 'index.json'), 'w'), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--strict', action='store_true')
    ap.add_argument('--no-archive', action='store_true')
    a = ap.parse_args()
    month = datetime.date.today().strftime('%Y-%m')
    rejected = []
    for path in a.files:
        if not os.path.exists(path):
            print(f'{path}: not present, skipping'); continue
        if not changed(path):
            print(f'{path}: unchanged'); continue
        ok, why = check(path)
        if ok:
            print(f'{path}: accepted -- {why}')
            if not a.no_archive:
                archive(path, month)
        else:
            print(f'::warning::{path}: REJECTED and restored to the last good version -- {why}')
            restore(path); rejected.append(path)
    if not a.no_archive:
        prune_and_index()
    if rejected and a.strict:
        sys.exit(1)


if __name__ == '__main__':
    main()
