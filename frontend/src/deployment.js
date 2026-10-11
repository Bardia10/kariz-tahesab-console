// Vite's base keeps the standalone console isolated under its deployment path.
export function consoleUrl(path) {
  return `${import.meta.env.BASE_URL}${path.replace(/^\//, '')}`;
}
