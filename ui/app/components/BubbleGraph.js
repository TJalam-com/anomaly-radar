"use client";
// V2 bubble map (G6 M2–M4). Canvas via cytoscape.js; the node list beside it mirrors the canvas (M3 sync), and the
// complete DOM node table on the page is the measurable source (M6). Node size = net shares at T_ref; colour = score.
import { useEffect, useRef, useState } from "react";
import { scoreColour } from "@/lib/colour";

const HATCH = "data:image/svg+xml;utf8," + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#e2e8f0"/><path d="M0 8L8 0" stroke="#64748b" stroke-width="1.5"/></svg>');


export default function BubbleGraph({ nodes, edges, walletHref, trustText }) {
  const ref = useRef(null);
  const cyRef = useRef(null);
  const [sel, setSel] = useState(null);
  useEffect(() => {
    let cy;
    let alive = true;
    import("cytoscape").then(({ default: cytoscape }) => {
      if (!alive || !ref.current) return;
      const maxS = Math.max(...nodes.map((n) => n.shares), 1);
      cy = cytoscape({
        container: ref.current,
        elements: [
          ...nodes.map((n) => ({ data: { id: n.wallet, size: 12 + 48 * Math.sqrt(n.shares / maxS), colour: scoreColour(n.score), na: n.score == null ? 1 : 0 } })),
          ...edges.map((e, i) => ({ data: { id: `e${i}`, source: e.a, target: e.b, type: e.type } })),
        ],
        style: [
          { selector: "node", style: { width: "data(size)", height: "data(size)", "background-color": "data(colour)", "border-width": 1, "border-color": "#334155" } },
          { selector: "node[na = 1]", style: { "background-image": HATCH, "background-color": "#e2e8f0", "border-style": "dashed" } },
          { selector: "edge", style: { width: 1, "line-color": "#f59e0b", "line-style": "dashed", opacity: 0.7 } },
          { selector: ":selected", style: { "border-width": 3, "border-color": "#0f172a" } },
        ],
        layout: { name: "cose", animate: false, randomize: false, nodeRepulsion: 6000 },
        wheelSensitivity: 0.2,
      });
      cy.on("tap", "node", (ev) => setSel(ev.target.id()));
      cyRef.current = cy;
    });
    return () => { alive = false; cy?.destroy(); };
  }, [nodes, edges]);
  const pick = (w) => { setSel(w); const cy = cyRef.current; if (cy) { cy.$(":selected").unselect(); cy.$id(w).select(); cy.center(cy.$id(w)); } };
  return (
    <div className="grid gap-3 md:grid-cols-[1fr_280px]">
      <div ref={ref} className="h-[520px] rounded border bg-white" data-testid="bubble-canvas" aria-label="bubble map of wallets holding this market at T_ref" />
      <div className="h-[520px] overflow-y-auto rounded border bg-white p-2 text-xs" data-testid="graph-node-list">
        <div className="font-semibold">Graph nodes ({nodes.length}) — click to locate</div>
        {nodes.map((n) => (
          <button key={n.wallet} type="button" onClick={() => pick(n.wallet)} data-graph-node={n.wallet}
            className={`block min-h-[28px] w-full text-left font-mono ${sel === n.wallet ? "bg-slate-200" : "hover:bg-slate-50"}`} aria-label={`select ${n.wallet} in the graph`}>
            {n.wallet.slice(0, 8)}… {n.score == null ? "NA" : n.score.toFixed(2)}{" "}
            <span className="trust-icon" role="img" aria-label={trustText} title={trustText}><span aria-hidden="true">◌</span></span>
            {n.s6_incomplete ? <span data-s6-incomplete="1" className="ml-1 text-slate-600">link found; transfer history incomplete</span> : null}
          </button>
        ))}
        {sel && <a className="mt-2 block text-sky-700 hover:underline" href={(walletHref || "/wallet/").includes("?") ? walletHref.replace("/wallet/?", `/wallet/${sel}?`) : `${walletHref || "/wallet/"}${sel}`}>open wallet page for {sel.slice(0, 8)}… →</a>}
      </div>
    </div>
  );
}
