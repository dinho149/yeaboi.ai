# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in yeaboi, please report it privately so
it can be fixed before public disclosure.

- **Preferred:** open a [GitHub private security advisory](https://github.com/omardin14/yeaboi.ai/security/advisories/new)
  (Security → Advisories → *Report a vulnerability*).
- **Alternatively:** email **onoureldin@gmail.com** with the details.

Please include: a description of the issue, the affected version, steps to
reproduce (a proof of concept if you have one), and the impact you foresee.
Do **not** open a public issue for a suspected vulnerability.

We aim to acknowledge reports within a few days and to ship a fix or mitigation
as quickly as the severity warrants. You'll be credited in the release notes
unless you prefer to remain anonymous.

## Supported versions

yeaboi ships from `main` to PyPI. Security fixes target the latest released
version; please upgrade (`uv tool install --upgrade yeaboi` /
`pipx upgrade yeaboi`) before reporting to confirm the issue still reproduces.

## Threat model & notes for users

yeaboi is a local terminal tool. A few features have deliberate trust boundaries
worth understanding:

- **The live boards (Retro and Poker) bind loopback only.** Nothing on your
  network can reach them directly — not the office Wi-Fi, not the coffee shop.
  Earlier versions bound all interfaces and advertised a LAN URL; that is gone.
- **They are shared through a public Cloudflare tunnel, which starts with the
  board.** While the board is open it is reachable from the internet over HTTPS.
  Access to `/api/*` is gated by a 128-bit token, the board page itself is behind
  a short join code, and the join endpoint is rate-limited **per visitor** — but
  anyone with the link *and* the code can participate. Close the board when the
  ceremony ends. The host's own link additionally carries an admin secret
  (reveal, save, edit, timer, lock); never paste it anywhere you would paste the
  invite.
- **"Share Online" opens the same kind of tunnel for a single static output**,
  behind the same access-code gate, for as long as the sharing screen is open.
- **Every tunnel auto-closes after an hour by default** (`TUNNEL_TIMEOUT_MINUTES`,
  adjustable — including switching it off entirely — from Settings > System), so
  a board or share left open by accident doesn't stay internet-reachable
  indefinitely. Closing the screen yourself still closes it immediately either way.
  A retro or poker ceremony can run past an hour, and a quick tunnel gets a fresh
  random URL on every relaunch, so the live boards warn the host in the status
  line as expiry nears — with time to wrap up or re-share — rather than the link
  just going dead mid-session. Raise `TUNNEL_TIMEOUT_MINUTES` for routinely longer
  ceremonies.
- **What the tunnel can and cannot reach.** It forwards to exactly one origin:
  the board's own loopback port. It is not a route to the rest of your machine.
  No request handler opens a file from anything a visitor sends — the pages are
  built from bundles compiled into the package, and there is no static-file route
  to traverse. What a participant can see is the board's contents and what other
  participants put there.
- **The one exception is the poker duel's microphone**, which records on *your*
  machine. It now requires you to arm it locally in the TUI first; an admin link
  alone cannot switch it on.
- **A verified-users tier is available, and is off by default.** The TUI offers
  it once, right before your first share; after that the Sharing tab in
  Settings (or `yeaboi --setup-access`) walks the setup: it signs in to
  Cloudflare, picks or creates a tunnel named `yeaboi`, points a DNS record at
  it, installs the `access` extra, and stores the result — you answer three
  things (your domain, team, AUD — boards are served at boards.<domain>), and
  it needs a domain already added to your Cloudflare account. Creating the Access
  application stays manual — automating it would require a zone-scoped
  Cloudflare API token that can also create tunnels and DNS records, and yeaboi
  deliberately holds no Cloudflare API token. Once on, the share is served on
  **your** hostname behind Cloudflare Access instead of a random
  `trycloudflare.com` one. Three things change:
  - **Every tunnel-borne request must carry a Cloudflare Access token, and
    yeaboi verifies it locally** against Cloudflare's published signing keys —
    signature, audience, issuer and expiry — rather than trusting the edge's
    header. The board token stops being a way in, so a leaked link is not one.
    Your own browser on `127.0.0.1` is unaffected and still uses the token.
  - **Identity stops being self-asserted.** Card ownership and the name on a
    card come from the verified token, not from a value the browser chose, so a
    participant cannot act as someone else.
  - **Host powers come from `CLOUDFLARE_ACCESS_ADMIN_EMAILS`**, not from the
    admin secret in a URL — which also keeps that secret out of Cloudflare's
    edge access log. The microphone gate above becomes accountable to a named
    person.

  It **never silently falls back** to a quick tunnel: if the tier is
  misconfigured or Cloudflare's signing keys cannot be fetched, the board stays
  on `127.0.0.1` and the TUI says which piece is missing. A share you believe is
  behind your identity provider but is not is worse than no share at all.
- **`cloudflared` is auto-downloaded** from a pinned Cloudflare release and
  verified against a bundled SHA-256 before it is made executable or run. On
  every later launch its digest is re-checked against one recorded at install
  time (a corruption check — an attacker who can rewrite the binary in
  `~/.yeaboi/bin`, itself `0700`, could rewrite the recorded digest too), and it
  runs with a **minimal environment** — it never sees your API keys, and an
  environment variable cannot reconfigure it.
  Set `YEABOI_CLOUDFLARED_STRICT=1` to refuse any `cloudflared` other than that
  pinned, verified copy (by default a binary already on your `PATH` is used if
  present, which is convenient but unverified).
- **Credentials are stored in `~/.yeaboi/.env`** (plaintext, `0600`, in a `0700`
  directory). Anyone with read access to your account can read them — treat that
  file like any other secrets file and never commit it.
- **External content (Jira/Confluence/Notion tickets, git commits, retro cards,
  1:1 transcripts) is fed to the LLM.** The agent's tools are read-only, which
  limits the blast radius of prompt injection, but treat generated output as
  untrusted when it is rendered or delivered.

## Automated scanning

Every pull request runs SAST (ruff flake8-bandit rules), a dependency CVE audit
(`pip-audit` against the committed `uv.lock`), and secret scanning (gitleaks).
Dependabot proposes dependency and GitHub Actions updates weekly, and a scheduled
workflow re-scans `main` and opens a fix PR for any new finding. Run the same
checks locally with `make security`.
