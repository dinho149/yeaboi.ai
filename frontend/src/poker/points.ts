/**
 * Formatting story points, which are integers that arrive as floats.
 *
 * The board stores points as `float | None` because a median can land on 6.5,
 * but almost every value people actually see is a whole number. Rendering the
 * raw float puts "3.0" on cards, in the rail, and in the finalize box, which
 * reads as a precision the estimate does not have.
 */

/** `3.0` → `"3"`, `6.5` → `"6.5"`, `null` → `"—"`. */
export function fmtPoints(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return Number.isInteger(value) ? String(Math.trunc(value)) : String(value);
}
