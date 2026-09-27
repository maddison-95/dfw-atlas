# Internal lending signals — read this before sharing the link with anyone

This page (`internal-lending/index.html`) is deliberately **separate from the public map** — it is not linked
from `index.html`, it's excluded from search engines via `robots.txt` and a `noindex` tag, and it sits behind a
name/email/mobile registration form with a captcha.

**What it shows:** aggregate HMDA (Home Mortgage Disclosure Act) lending activity for the 5 atlas counties —
originated loan counts and dollar volume, split by loan purpose (purchase / refinance / cash-out refinance) and
by construction method (site-built / manufactured home). This is the same public aggregate data anyone can
pull from the CFPB's own HMDA data browser (https://ffiec.cfpb.gov/data-browser/) — nothing here identifies an
individual borrower or lender, and it never requests race, ethnicity, sex, or any other protected-class field.

**Why it's separate from the public map:** showing lending-market data next to a consumer-facing map that also
markets your loan programs could read as fair-lending steering, even with entirely defensible data. Keeping it
off the public map sidesteps that question rather than relying on a judgment call about what's fine.

## The gate: registration, not a passphrase

Visitors now register (name, email, mobile) and solve a captcha before they see anything — replacing the
earlier shared passphrase. Every successful registration emails you the details. There are no different
permission levels; it's a single yes/no gate, same as before, just backed by real information about who's
asking instead of a password anyone could pass along.

**What's genuinely stronger than before:** a live captcha (stops scripted/automated attempts), a real
name/email/mobile captured per person (not one password shared by everyone), and you get a real-time email
every time someone gets in.

**What's still not real access control:** this is still a static site (GitHub Pages) with no server of its own
to sit in front of the page and check anything *before* serving it. The registration form is enforced by the
page's own JavaScript, so someone who already has the exact link — or the data file's URL directly
(`data/hmda_stats.json`) — can still reach the content without ever filling out the form; registering only
stops a visitor who lands on the page and behaves like one. That gap is inherent to any static site, not
something this particular gate does wrong. If it ever matters — the link starts circulating beyond people you
handed it to directly, or this needs to hold something more sensitive — the real fix is checking access in
front of GitHub Pages itself (e.g. Cloudflare Access), not inside the page. Tell Claude and we'll set that up;
it doesn't require moving off GitHub Pages.

### Setting it up (about 20 minutes, three free accounts, no credit card)

The registration form needs one small piece of server code to check the captcha and send you the email —
GitHub Pages can't do either on its own (both need a secret key that must never be visible in the page's
source). That piece is a **Cloudflare Worker**, and it's free.

1. **Website — Cloudflare account.** Go to https://dash.cloudflare.com/sign-up, create a free account (no
   card needed). You do **not** need to move your domain's DNS to Cloudflare for any of this.
2. **Website — Turnstile (the captcha).** In the Cloudflare dashboard, left sidebar → **Turnstile** → **Add
   site**. Name it anything (e.g. "DFW Atlas internal"), domain `maps.michaeladdison.ai`, widget mode
   "Managed". Create it, then copy the **Site Key** and **Secret Key** it gives you — you'll need both below.
3. **Website — Resend account (sends the email).** Go to https://resend.com/signup, create a free account
   (100 emails/day, 3,000/month, no card). Fastest path: skip domain verification for now and use their shared
   test sender `onboarding@resend.dev` as `FROM_EMAIL` below — email still lands in your inbox fine, it just
   shows "via resend.dev". (Later, if you want it to say "from mail.michaeladdison.ai" instead: Resend
   dashboard → Domains → Add Domain → it gives you 2-3 DNS records to add at GoDaddy, same as the
   `maps.michaeladdison.ai` CNAME you already added — takes a few minutes, verification can take up to an
   hour.) Then Resend dashboard → **API Keys** → **Create API Key** → copy it.
4. **Website — deploy the Worker.** Cloudflare dashboard → **Workers & Pages** → **Create** → **Workers** →
   name it (e.g. `il-register`) → **Deploy** (this deploys Cloudflare's placeholder "Hello World" code; that's
   fine, you're about to replace it). Click **Edit code**, select all the existing code and delete it, then
   paste in the entire contents of `internal-lending/worker/register.js` from this folder. Click **Deploy**.
5. **Website — set the four secrets.** Still on that Worker: **Settings** → **Variables and Secrets** → **Add**
   → add each of these four, all as type **Secret** (not "Text"/plain variable — secrets are encrypted, plain
   variables aren't):
   - `TURNSTILE_SECRET` — the Secret Key from step 2
   - `RESEND_API_KEY` — the API key from step 3
   - `FROM_EMAIL` — `onboarding@resend.dev` (or your verified address from step 3)
   - `NOTIFY_EMAIL` — your own inbox address, where registrations should land
   Save. Cloudflare shows the Worker's URL near the top of its page, something like
   `https://il-register.YOURSUBDOMAIN.workers.dev` — copy it.
   **5b. Rate limiting (2 minutes, recommended).** Without this, one person clicking Register over and over
   could use up Resend's 100-emails-a-day free allowance. Cloudflare dashboard → **Storage & Databases** →
   **KV** → **Create** → name it `il-rate` → Create. Then back on the Worker: **Settings** → **Bindings** →
   **Add** → **KV namespace** → Variable name exactly `RATE_KV`, namespace `il-rate` → Save (it redeploys).
   That's it — the Worker now allows at most 5 registrations per hour from one connection, 3 per day per
   email address, and 60 per day overall; over the limit it returns a plain-English message instead of
   sending an email. If you skip this step everything still works, just without the caps.
6. **Website — wire the two values into the page.** Edit `internal-lending/index.html` in this repo (GitHub's
   own web editor is fine for two small edits — click the file, pencil/edit icon, ⌘F or Ctrl+F to find each):
   - find `data-sitekey="%%TURNSTILE_SITE_KEY%%"` → replace `%%TURNSTILE_SITE_KEY%%` with the Site Key from
     step 2
   - find `const REGISTER_URL = '%%WORKER_URL%%';` → replace `%%WORKER_URL%%` with the Worker URL from step 5
   Commit. GitHub Pages redeploys automatically a minute or two later.
7. **Test it.** Open the page in a private/incognito window, fill in the form with your own info, solve the
   captcha, submit. You should land on the page's content and get an email within a few seconds. If something's
   off, the on-page error message says why (missing captcha, bad email format, etc.) — if it says it can't
   reach the registration service, double check the Worker URL was pasted correctly and has no trailing spaces.

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
