from __future__ import annotations

import socket
import time
from typing import Optional

import httpx


def probe(target: str = "huggingface.co", proxy: Optional[str] = None, timeout: float = 5.0) -> dict:
    """Return a dict diagnosing connectivity. No side effects, no subprocess."""
    result = {"target": target, "proxy": proxy, "dns": False, "direct": False, "via_proxy": None}

    try:
        socket.gethostbyname(target)
        result["dns"] = True
    except socket.gaierror as e:
        result["dns_error"] = str(e)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.head(f"https://{target}")
            result["direct"] = r.status_code < 500
            result["direct_status"] = r.status_code
    except Exception as e:
        result["direct_error"] = f"{type(e).__name__}: {e}"

    if proxy:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, proxy=proxy) as c:
                r = c.head(f"https://{target}")
                result["via_proxy"] = r.status_code < 500
                result["proxy_status"] = r.status_code
        except Exception as e:
            result["via_proxy"] = False
            result["proxy_error"] = f"{type(e).__name__}: {e}"

    return result


def run_loop(target: str, proxy: Optional[str], interval: int) -> int:
    print(f"netmon target={target} proxy={proxy or '-'} interval={interval}s (warn-only, no auto recovery)")
    consec_fail = 0
    try:
        while True:
            r = probe(target, proxy)
            ok = r["dns"] and (r["via_proxy"] if proxy else r["direct"])
            ts = time.strftime("%H:%M:%S")
            if ok:
                if consec_fail:
                    print(f"[{ts}] RECOVERED after {consec_fail} fails")
                consec_fail = 0
                print(f"[{ts}] OK   dns={r['dns']} direct={r['direct']} via_proxy={r['via_proxy']}")
            else:
                consec_fail += 1
                print(f"[{ts}] FAIL ({consec_fail}x)  {r}")
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
