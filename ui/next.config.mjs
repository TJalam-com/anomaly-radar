/** Anomaly Radar (G6). Read-only, localhost only. No browser source maps (U13); no x-powered-by header.
 *  distDir from ANOMALY_RADAR_DIST so dev builds (:3001) never touch the review build (:3000, .next). */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  distDir: process.env.ANOMALY_RADAR_DIST || ".next",
};
export default nextConfig;
