// Production launcher: cwd = this app dir (data/ is read relative to it), telemetry off, localhost only.
// usage: node serve.js [--port 3000] [--dist .next] [--data data]   (review: 3000/.next/data; dev: 3001/.next-dev/data-dev)
const path = require("node:path");
process.chdir(__dirname);
process.env.NEXT_TELEMETRY_DISABLED = "1";
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const port = arg("--port", "3000");
process.env.ANOMALY_RADAR_DIST = arg("--dist", ".next");
process.env.ANOMALY_RADAR_DATA = arg("--data", "data");
const bin = path.join(__dirname, "node_modules", "next", "dist", "bin", "next");
process.argv = [process.argv[0], bin, "start", "-H", "127.0.0.1", "-p", port];
require(bin);
