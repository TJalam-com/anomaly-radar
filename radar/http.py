import time

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class Fetcher:
    """GET returning (status, raw bytes). Retries 429/5xx with backoff. Transport injectable for tests."""

    def __init__(self, transport: httpx.BaseTransport | None = None, retries: int = 5, sleep=time.sleep):
        self.client = httpx.Client(
            transport=transport, timeout=60, follow_redirects=False,
            headers={"User-Agent": "anomaly-radar/0.1 (read-only research)"},
        )
        self.retries = retries
        self.sleep = sleep

    def get(self, url: str, params: dict | None = None) -> tuple[int, bytes, str]:
        for attempt in range(self.retries):
            try:
                r = self.client.get(url, params=params)
            except httpx.TransportError:
                if attempt == self.retries - 1:
                    raise
                self.sleep(2 ** attempt)
                continue
            if r.status_code in RETRY_STATUS and attempt < self.retries - 1:
                self.sleep(2 ** attempt)
                continue
            return r.status_code, r.content, str(r.url)
        raise RuntimeError("unreachable")
