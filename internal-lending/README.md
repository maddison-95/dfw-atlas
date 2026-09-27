# Internal lending signals — read this before sharing the link with anyone

This page (`internal-lending/index.html`) is deliberately **separate from the public map** — it is not linked
from `index.html`, it's excluded from search engines via `robots.txt` and a `noindex` tag, and it sits behind a
passphrase.

**What it shows:** aggregate HMDA (Home Mortgage Disclosure Act) lending activity for the 5 atlas counties —
originated loan counts and dollar volume, split by loan purpose (purchase / refinance / cash-out refinance) and
by construction method (site-built / manufactured home). This is the same public aggregate data anyone can
pull from the CFPB's own HMDA data browser (https://ffiec.cfpb.gov/data-browser/) — nothing here identifies an
individual borrower or lender, and it never requests race, ethnicity, sex, or any other protected-class field.

**Why it's separate from the public map:** showing lending-market data next to a consumer-facing map that also
markets your loan programs could read as fair-lending steering, even with entirely defensible data. Keeping it
off the public map sidesteps that question rather than relying on a judgment call about what's fine.

## About the passphrase gate — please read this part

The gate is a **speed bump, not real security**. This is a static site (GitHub Pages) with no server to check a
password against — the check happens in the visitor's own browser, comparing what they typed to a hash sitting
right there in the page's source code. Anyone who already has the link, or who views source, can get past it or
skip it entirely. It's appropriate for exactly the situation you described — nobody knows this page exists yet
— and no further than that. If you start sharing this link with other people, or if it needs to hold anything
more sensitive later, tell Claude and we'll set up real access control (for example, Cloudflare Access sitting
in front of the domain, which can require a login before anyone even reaches GitHub Pages).

**Default passphrase:** `dfw-signals-26` — change it. Pick a new one, then run this in any browser's
JavaScript console (F12 → Console tab) and paste the result into `index.html` in place of the `HASH` constant
near the top of the `<script>` block:
```js
crypto.subtle.digest('SHA-256', new TextEncoder().encode('your new passphrase')).then(b=>console.log([...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')))
```

## Data refresh

`data/hmda_stats.json` is written by `scripts/build_hmda.py`, which now runs automatically as part of the same
monthly Action as the housing/schools/lot data (**Refresh housing + schools + lot + lending data**) — HMDA data
itself only updates a few times a year, so most months this step will find nothing changed, which is normal.

## Known gaps (fast-follow candidates, not built yet)

* **No lender-level breakdown yet.** HMDA's raw records only carry each lender's LEI (a long ID code), not a
  readable name — matching that to "Acme Homes Mortgage" needs a second lookup against a public LEI registry
  (GLEIF). Worth doing once you're using the county-level numbers and want to know *who's* originating that
  volume, not just how much.
* **"Manufactured home" isn't "new construction."** HMDA's construction-method field distinguishes site-built
  from manufactured homes; it isn't a builder/teardown/spec-build signal. A real new-construction-lending
  signal would need a different source (e.g. the Census building-permits data already on the project's to-do
  list) cross-referenced with this.
