/**
 * Join class names, dropping anything falsy.
 *
 * Exists because CSS Modules are typed as an index signature and this project
 * builds with `noUncheckedIndexedAccess`, so `styles.foo` is `string |
 * undefined`. A template literal would happily interpolate the string
 * "undefined" into a className; this cannot.
 *
 * Component prop interfaces declare `className?: string | undefined` rather
 * than `className?: string`, because `exactOptionalPropertyTypes` is on and
 * callers forward optionals into it constantly (`className={styles['x']}`,
 * where the CSS-Modules index signature makes that `string | undefined`). The
 * absent-versus-present-and-undefined distinction is load-bearing in the
 * runtime layer and meaningless for a class name. See AvatarProps.
 */
export const cx = (...parts: (string | false | null | undefined)[]): string =>
  parts.filter(Boolean).join(' ');
