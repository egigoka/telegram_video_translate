import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

let undiciFetch;
const candidates = [
  "/usr/local/lib/node_modules/vot-cli/node_modules/undici/index.js",
  "/usr/lib/node_modules/vot-cli/node_modules/undici/index.js",
  "/usr/local/lib/node_modules/undici/index.js",
  "/usr/lib/node_modules/undici/index.js",
];
for (const path of candidates) {
  try {
    undiciFetch = require(path).fetch;
    break;
  } catch {
    // try next candidate
  }
}
if (!undiciFetch) {
  try {
    undiciFetch = require(
      require.resolve("undici", {
        paths: ["/usr/local/lib/node_modules", "/usr/lib/node_modules"],
      }),
    ).fetch;
  } catch {
    // fall back to Node's native fetch (undici-based since Node 18)
  }
}

if (undiciFetch) {
  globalThis.fetch = undiciFetch;
}
