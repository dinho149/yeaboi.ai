// electron-vite's `?asset` import: the file is copied into the build output and
// the import resolves to its runtime path, in dev and packaged alike. It is how
// main reaches a real file (the tray icon) without guessing at directories.

declare module '*.png?asset' {
  const path: string;
  export default path;
}
