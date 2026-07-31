/**
 * URL scheme gating — the browser half of `yeaboi.html_theme.safe_url`.
 *
 * Tracker URLs reach these pages from Jira / Azure DevOps / GitHub payloads and
 * from a user-configured base URL, so they are attacker-influenced. **React does
 * not save you here**: it warns about a `javascript:` href in development and
 * renders it anyway in production, which is exactly backwards for a bundle whose
 * only shipped form *is* the production build.
 *
 * Kept deliberately in lockstep with the Python function — same allowlist, same
 * whitespace handling, same protocol-relative rejection — because a ticket
 * rendered by the poker board and the same ticket rendered into an HTML export
 * must not disagree about what is safe.
 */

/** Schemes allowed to reach an `href`. Tiny on purpose: a tracker or a person. */
const SAFE_SCHEMES = new Set(['http', 'https', 'mailto']);

/** A scheme per RFC 3986: ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ) ":" */
const SCHEME_RE = /^([A-Za-z][A-Za-z0-9+.-]*):/;

/**
 * TAB / LF / CR are removed from *anywhere* in a URL by the URL parser before
 * the scheme is read, so `java&#9;script:alert(1)` arrives as `javascript:`.
 * Strip exactly those, or the allowlist below is trivially bypassed. Interior
 * spaces are deliberately kept — browsers keep them too, so they cannot smuggle
 * a scheme past this check.
 */
const STRIP_RE = /[\t\n\r]/g;

/**
 * Leading/trailing whitespace and C0 controls the URL parser also ignores.
 * The same set `html_theme.safe_url` strips, written with escapes so the
 * characters stay visible in the source rather than sitting there as raw bytes.
 */
const TRIM_RE = /^[\x00-\x20\x7f]+|[\x00-\x20\x7f]+$/g;

/**
 * Return `url` when it is safe to place in an `href`, else `''`.
 *
 * A value with no scheme at all (`example.com/browse/KEY`, `/browse/KEY`) comes
 * back unchanged: with no scheme the browser resolves it against the document
 * and it cannot execute. Protocol-relative `//host` is rejected — under
 * `file://` it resolves to a bogus origin, and it is never what a caller meant.
 */
export function safeUrl(url: string | null | undefined): string {
  if (!url) return '';
  const cleaned = String(url).replace(TRIM_RE, '').replace(STRIP_RE, '');
  if (!cleaned) return '';
  if (cleaned.startsWith('//')) return '';
  const match = SCHEME_RE.exec(cleaned);
  if (match === null) return cleaned;
  return SAFE_SCHEMES.has((match[1] ?? '').toLowerCase()) ? cleaned : '';
}

/** Image `src` values these pages may set. `data:` covers the inlined QR code. */
const IMAGE_DATA_RE = /^data:image\/(png|jpeg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$/;

/**
 * Return `value` when it is a base64 `data:image/...` URI, else `''`.
 *
 * Separate from {@link safeUrl} because `data:` is deliberately **not** in that
 * allowlist: `data:text/html,<script>…</script>` in an href navigates to an
 * attacker-controlled document. It is safe only in the narrow `<img src>` case,
 * and only for the image media types listed above.
 */
export function safeImageSrc(value: string | null | undefined): string {
  if (!value) return '';
  const cleaned = String(value).replace(TRIM_RE, '').replace(STRIP_RE, '');
  if (IMAGE_DATA_RE.test(cleaned)) return cleaned;
  // Not a data: URI — fall through to the ordinary scheme gate, so a same-origin
  // relative path (`/api/qr?token=…`, how the invite QR is actually loaded) works.
  const safe = safeUrl(cleaned);
  return safe.toLowerCase().startsWith('data:') ? '' : safe;
}
