/*
 * Cloudflare Worker: verifies a Turnstile captcha, emails Michael the registrant's name/email/mobile,
 * and tells the page whether to let the visitor in. This is the piece a static site (GitHub Pages)
 * can't do on its own -- everything else in this project runs without a server; this one small piece
 * needs one, because checking a captcha and sending an email both require a secret key that must
 * never be visible to a visitor's browser.
 *
 * Deploy: Cloudflare dashboard -> Workers & Pages -> Create -> "Deploy" a Hello World Worker, then
 * "Edit code", replace everything with this file, Save and Deploy. Full walkthrough, including where
 * the four secrets below come from, is in internal-lending/README.md.
 *
 * Secrets this Worker needs (Worker -> Settings -> Variables and Secrets -> add each as "Secret",
 * never as a plain "Variable" -- secrets are encrypted, variables are not):
 *   TURNSTILE_SECRET   the Turnstile widget's secret key (paired with the site key used in index.html)
 *   RESEND_API_KEY     your Resend API key
 *   FROM_EMAIL         a sender address at a domain verified in Resend, e.g. "DFW Atlas <notify@mail.michaeladdison.ai>"
 *   NOTIFY_EMAIL       where the registration notification goes -- your own inbox
 *
 * Only requests from ALLOWED_ORIGIN are allowed to call this (CORS) -- change it if your map's domain
 * ever changes.
 */
const ALLOWED_ORIGIN = 'https://maps.michaeladdison.ai';

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return withCors(new Response(null, { status: 204 }));
    if (request.method !== 'POST') return withCors(json({ ok: false, error: 'method not allowed' }, 405));

    let body;
    try { body = await request.json(); } catch (e) { return withCors(json({ ok: false, error: 'bad request body' }, 400)); }
    const name = String(body.name || '').trim();
    const email = String(body.email || '').trim();
    const mobile = String(body.mobile || '').trim();
    const token = String(body.token || '');
    if (!name || !email || !mobile || !token) return withCors(json({ ok: false, error: 'all fields are required' }, 400));
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return withCors(json({ ok: false, error: 'that email address doesn’t look right' }, 400));

    // 1) verify the captcha server-side (this is the step a pure client-side check can't do honestly --
    //    only the Turnstile secret key, held here, can confirm the token is real and unused)
    const verifyRes = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        secret: env.TURNSTILE_SECRET, response: token,
        remoteip: request.headers.get('CF-Connecting-IP') || '',
      }),
    });
    const verify = await verifyRes.json();
    if (!verify.success) return withCors(json({ ok: false, error: 'captcha check failed -- please try again' }, 400));

    // 2) email Michael the registration (best-effort -- a failed email should never be the reason a
    //    real person can't get in, so this never blocks the ok:true response below)
    try {
      const emailRes = await fetch('https://api.resend.com/emails', {
        method: 'POST',
        headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from: env.FROM_EMAIL,
          to: env.NOTIFY_EMAIL,
          subject: 'DFW Atlas: new internal-lending registration',
          text: `Name: ${name}\nEmail: ${email}\nMobile: ${mobile}\nWhen: ${new Date().toISOString()}\nIP: ${request.headers.get('CF-Connecting-IP') || 'unknown'}`,
        }),
      });
      if (!emailRes.ok) console.log('resend send failed:', await emailRes.text());
    } catch (e) {
      console.log('resend send threw:', e);
    }

    return withCors(json({ ok: true }));
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json' } });
}
function withCors(res) {
  res.headers.set('Access-Control-Allow-Origin', ALLOWED_ORIGIN);
  res.headers.set('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.headers.set('Access-Control-Allow-Headers', 'Content-Type');
  return res;
}
