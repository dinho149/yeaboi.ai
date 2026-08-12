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
  a short join code, and the join endpoint is rate-limited — but anyone with the
  link *and* the code can participate. Close the board when the ceremony ends.
  The host's own link additionally carries an admin secret (reveal, save, edit,
  timer, lock); never paste it anywhere you would paste the invite.
- **"Share Online" opens the same kind of tunnel for a single static output**,
  behind the same access-code gate, for as long as the sharing screen is open.
- **Every tunnel auto-closes after an hour by default** (`TUNNEL_TIMEOUT_MINUTES`,
  adjustable — including switching it off entirely — from Settings > System), so
  a board or share left open by accident doesn't stay internet-reachable
  indefinitely. Closing the screen yourself still closes it immediately either way.
- **`cloudflared` is auto-downloaded** from a pinned Cloudflare release and
  verified against a bundled SHA-256 before it is made executable or run.
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
