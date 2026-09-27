import "./globals.css";

export const metadata = {
  title: "Anomaly Radar",
  description: "Anomalous pattern scores for Polymarket wallets. Read-only. Scores are probabilistic, not proof.",
};

// The run-dependent parts (banner, run selector, nav, address search) are in each page's RunBar: the root layout
// cannot read the ?run= query value.
export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="bg-slate-50 text-slate-900 antialiased">
        <header className="border-b bg-white">
          <div className="mx-auto max-w-7xl px-4 py-3">
            <a href="/" data-testid="brand" className="text-lg font-semibold tracking-tight">Anomaly Radar</a>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-4">{children}</main>
        <footer className="mx-auto max-w-7xl px-4 pb-8 text-xs text-slate-600">
          <p data-testid="disclaimer" className="border-t pt-3">
            Scores are probabilistic indicators of anomalous trading patterns, not proof of anything, and make no claim about
            the identity of any person behind a wallet. Public on-chain and public API data only. Read-only: no trading.
          </p>
        </footer>
      </body>
    </html>
  );
}
