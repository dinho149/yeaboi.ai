/**
 * WCAG relative luminance and contrast, for the theme audit in `theme.test.ts`.
 *
 * Test-support code that lives in `src/` rather than `src/test/` on purpose: it
 * is imported by a test but describes a property of the design system, and
 * keeping it next to the palette is what makes it likely to be looked at when
 * someone edits a theme.
 *
 * Nothing in the shipped bundles imports it, so it is tree-shaken out.
 */

/** WCAG 2.2 SC 1.4.3 — normal-size text. */
export const AA_TEXT = 4.5;
/** WCAG 2.2 SC 1.4.11 — UI components and graphical objects. */
export const AA_NON_TEXT = 3;

/** Parse `#rrggbb` (or `#rgb`) into 0–1 channels. Throws on anything else. */
export function parseHex(hex: string): [number, number, number] {
  const value = hex.trim();
  const short = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(value);
  const full = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(value);
  if (short) {
    return [
      parseInt(`${short[1]}${short[1]}`, 16) / 255,
      parseInt(`${short[2]}${short[2]}`, 16) / 255,
      parseInt(`${short[3]}${short[3]}`, 16) / 255,
    ];
  }
  if (!full) throw new Error(`not a hex colour: ${hex}`);
  return [
    parseInt(full[1] as string, 16) / 255,
    parseInt(full[2] as string, 16) / 255,
    parseInt(full[3] as string, 16) / 255,
  ];
}

/** WCAG relative luminance. */
export function luminance(hex: string): number {
  const linear = parseHex(hex).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)) as [
    number,
    number,
    number,
  ];
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

/**
 * `color-mix(in srgb, a <weight>%, b)`, as a hex string.
 *
 * The `srgb` colour space interpolates the gamma-encoded channels directly, so
 * this is a plain linear blend of what `parseHex` returns — not the linearised
 * values `luminance` works in. Getting that backwards produces a colour that is
 * visibly right and measurably wrong, which is the worst kind of wrong for an
 * audit. Exists so a token *derived* in CSS can still be measured here: the
 * slide deck computes its panel and dim tiers from a six-colour palette, and
 * "the stylesheet does the mixing" is not a reason to stop checking contrast.
 */
export function mixSrgb(a: string, b: string, weightA: number): string {
  const ca = parseHex(a);
  const cb = parseHex(b);
  const channel = (i: number) => {
    const value = Math.round((ca[i]! * weightA + cb[i]! * (1 - weightA)) * 255);
    return Math.max(0, Math.min(255, value)).toString(16).padStart(2, '0');
  };
  return `#${channel(0)}${channel(1)}${channel(2)}`;
}

/** Contrast ratio between two hex colours, 1–21. Order does not matter. */
export function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Extract the `[data-theme="…"]` blocks from palette.css as token maps.
 *
 * Parsed from the stylesheet source rather than measured through
 * `getComputedStyle`, deliberately: jsdom does not resolve custom properties
 * across selectors, and reading the file is the only way to check a theme the
 * test is not currently displaying. All five get audited in one run.
 */
export function parsePalettes(css: string): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {};
  const blockRe = /\[data-theme="(\w+)"\][^{]*\{([^}]*)\}/g;
  for (let match = blockRe.exec(css); match !== null; match = blockRe.exec(css)) {
    const name = match[1] as string;
    const body = match[2] as string;
    const tokens: Record<string, string> = {};
    const tokenRe = /--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})/g;
    for (let t = tokenRe.exec(body); t !== null; t = tokenRe.exec(body)) {
      tokens[t[1] as string] = t[2] as string;
    }
    out[name] = tokens;
  }
  return out;
}

/**
 * Extract the `[data-mode="…"] { --accent: … }` overrides from tokens.css.
 *
 * The palette audit cannot see these: a mode replaces `--accent` *after* the
 * theme block, so what a retro board actually paints is midnight's surfaces
 * with retro's teal — a pairing that appears in no single palette block. Eight
 * modes times five themes is where an unreadable button hides, and did.
 *
 * Returns the two rungs separately. `base` applies to every theme; `light` is
 * the `[data-theme="light"][data-mode="…"]` compound, which is a darker
 * rendition of the same hue and wins on specificity. Parsing them into one map
 * would silently let the light value stand in for the dark one, since it comes
 * later in the file — exactly the kind of quiet overwrite this audit exists to
 * catch.
 */
export interface ModeAccents {
  base: Record<string, string>;
  light: Record<string, string>;
}

export function parseModeAccents(css: string): ModeAccents {
  const base: Record<string, string> = {};
  const light: Record<string, string> = {};
  const re = /(\[data-theme="(\w+)"\])?\[data-mode="(\w+)"\][^{]*\{[^}]*?--accent\s*:\s*(#[0-9a-fA-F]{3,8})/g;
  for (let m = re.exec(css); m !== null; m = re.exec(css)) {
    const theme = m[2];
    const mode = m[3] as string;
    const hex = m[4] as string;
    if (theme === undefined) base[mode] = hex;
    else if (theme === 'light') light[mode] = hex;
  }
  return { base, light };
}

/** The `--accent` a given (mode, theme) pair actually resolves to. */
export function effectiveAccent(accents: ModeAccents, mode: string, theme: string): string {
  const override = theme === 'light' ? accents.light[mode] : undefined;
  return override ?? (accents.base[mode] as string);
}
