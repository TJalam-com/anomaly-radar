"use client";
// Address with icon buttons (G6 C7, T-11): copy, Polygonscan (navigation only; no data sent), optional wallet page.
// Each control has a unique accessible name and a >= 24 px target. U2: address only, never a person.
import { useState } from "react";

const CopyIcon = () => <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="5" y="5" width="9" height="9" rx="1.5" /><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" /></svg>;
const ExtIcon = () => <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M9 2h5v5M14 2 7 9M12 9.5V13a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3.5" /></svg>;
const ArrowIcon = () => <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3 8h10M9 4l4 4-4 4" /></svg>;

export default function CopyAddress({ address, full = false, walletHref = null }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className={full ? "flex min-w-0 flex-wrap items-center gap-1" : "inline-flex items-center gap-1 whitespace-nowrap"} data-address={address}>
      <span className={`font-mono ${full ? "min-w-0 break-all text-sm" : "text-2xs"}`} title={address}>{full ? address : `${address.slice(0, 6)}…${address.slice(-4)}`}</span>
      <button type="button" className="icon-btn" aria-label={`copy address ${address}`} title="copy address"
        onClick={() => { navigator.clipboard?.writeText(address); setCopied(true); setTimeout(() => setCopied(false), 1200); }}>
        {copied ? <span className="text-2xs">✓</span> : <CopyIcon />}
      </button>
      <a className="icon-btn" href={`https://polygonscan.com/address/${address}`} target="_blank" rel="noopener noreferrer"
        aria-label={`open ${address} on Polygonscan (external)`} title="Polygonscan"><ExtIcon /></a>
      {walletHref && <a className="icon-btn" href={walletHref} aria-label={`wallet page for ${address}`} title="wallet page" data-testid="wallet-link"><ArrowIcon /></a>}
    </span>
  );
}
