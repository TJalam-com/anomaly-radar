// T-05: tag /wallet/<x> and /market/<x> requests with whether <x> is well-formed, so the 404 page (HTTP 404) can say
// "not a valid address" vs "not in this view". Format check only; no data access here.
import { NextResponse } from "next/server";

export function middleware(req) {
  const [, kind, seg] = req.nextUrl.pathname.split("/");
  const ok = kind === "wallet" ? /^0x[0-9a-fA-F]{40}$/.test(seg || "") : /^0x[0-9a-fA-F]{64}$/.test(seg || "");
  const headers = new Headers(req.headers);
  headers.set("x-ar-seg", `${kind}:${ok ? "valid" : "invalid"}`);
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: ["/wallet/:seg", "/market/:seg"] };
