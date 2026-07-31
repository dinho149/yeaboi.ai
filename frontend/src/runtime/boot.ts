/**
 * Reading the server's boot payload out of the page.
 *
 * `yeaboi.web.assets.render_page` emits it as
 * `<script type="application/json" id="yeaboi-data">…</script>`. That element
 * type is **not executable**: the browser parses it as data, so even a total
 * failure of the escaping on the Python side cannot get script to run. We read
 * it with `textContent` + `JSON.parse` — never `innerHTML`, which would hand
 * the string back to the HTML parser, and never `eval`, which the tunnel CSP
 * forbids outright.
 */

export const BOOT_ELEMENT_ID = 'yeaboi-data';

/**
 * Parse the boot payload, or return `null` when there isn't one.
 *
 * `null` is a real answer, not an error: the join gate deliberately ships with
 * no island at all, because an unauthenticated visitor must not learn the title
 * or mode of what is behind it.
 *
 * A payload that is present but malformed is a bug in the page we just served,
 * so it throws rather than degrading — a board silently booting with no config
 * is far harder to diagnose than a console error naming the element.
 */
export function readBoot<T>(id: string = BOOT_ELEMENT_ID): T | null {
  const el = document.getElementById(id);
  if (!el) return null;
  const raw = el.textContent ?? '';
  if (!raw.trim()) return null;
  try {
    return JSON.parse(raw) as T;
  } catch (err) {
    throw new Error(`#${id} is not valid JSON — the page was rendered wrong (${String(err)})`);
  }
}

/** Like {@link readBoot}, but for a page that cannot function without a payload. */
export function requireBoot<T>(id: string = BOOT_ELEMENT_ID): T {
  const value = readBoot<T>(id);
  if (value === null) throw new Error(`#${id} is missing — render_page was called without data=`);
  return value;
}
