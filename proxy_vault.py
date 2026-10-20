import asyncio
import base64
import json
import logging
import os
import re
import socket
import time
import urllib.parse
import urllib.request
from typing import List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [Vault] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("ProxyVault")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT_FILE = os.path.join(BASE_DIR, "claimed_proxies.json")
PROXY_TXT_FILE = os.path.join(BASE_DIR, "proxy.txt")
PROXIES_TXT_FILE = os.path.join(BASE_DIR, "proxies.txt")
CF_WORKER_URL = "https://sx.shkendj507.workers.dev"

MAX_CLAIMED_AGE = 7200  # 2 Hours

def format_proxy(p: dict) -> str:
    ip = str(p.get("ip") or p.get("host") or "").strip()
    port = str(p.get("port") or "").strip()
    login = str(p.get("login") or p.get("user") or "").strip()
    pwd = str(p.get("password") or p.get("pass") or "").strip()
    login = re.sub(r'[-_]pool[-_]res\.mobile\.corp', '-pool-res', login, flags=re.IGNORECASE)
    return f"{ip}:{port}:{login}:{pwd}"

def ping_proxy_tunnel(proxy_dict: dict, timeout: float = 2.5) -> Tuple[bool, str, float]:
    """Tests TCP CONNECT through the proxy to cp.cloudflare.com:80 and returns ms latency."""
    t0 = time.perf_counter()
    try:
        host = proxy_dict.get("ip") or proxy_dict.get("host")
        port = int(proxy_dict.get("port", 0))
        login = proxy_dict.get("login") or proxy_dict.get("user")
        pwd = proxy_dict.get("password") or proxy_dict.get("pass")
        if not (host and port and login and pwd):
            return False, "missing_credentials", 9999.0

        s = socket.create_connection((host, port), timeout=timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        auth = base64.b64encode(f"{login}:{pwd}".encode("utf-8")).decode("utf-8")
        req = f"CONNECT cp.cloudflare.com:80 HTTP/1.1\r\nHost: cp.cloudflare.com:80\r\nProxy-Authorization: Basic {auth}\r\nUser-Agent: Mozilla/5.0\r\n\r\n"
        s.sendall(req.encode("utf-8"))
        resp = s.recv(256)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        try:
            s.close()
        except Exception:
            pass
        if resp.startswith(b"HTTP/1.1 200") or resp.startswith(b"HTTP/1.0 200"):
            return True, "200 OK", elapsed_ms
        return False, resp.decode("latin-1", errors="ignore").strip(), elapsed_ms
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        return False, str(e), elapsed_ms

def _load_vault() -> List[dict]:
    if not os.path.exists(VAULT_FILE):
        return []
    try:
        with open(VAULT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                now = time.time()
                return [d for d in data if (now - d.get("claimed_at", 0)) < MAX_CLAIMED_AGE]
    except Exception:
        pass
    return []

def _save_vault(vault: List[dict]):
    tmp = VAULT_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(vault, f, indent=2)
        if os.path.exists(VAULT_FILE):
            os.remove(VAULT_FILE)
        os.rename(tmp, VAULT_FILE)
    except Exception as e:
        logger.error(f"Error saving vault: {e}")

def fetch_proxies_from_sx(token: str, session_id: str) -> List[dict]:
    """Fetches the 10 proxies from SX using Cloudflare Edge Worker."""
    # 1. Via Cloudflare Edge
    if CF_WORKER_URL:
        try:
            cf_url = f"{CF_WORKER_URL}/proxy?url=" + urllib.parse.quote("https://api.sx.org/proxy/short")
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://my.sx.org",
                "Referer": "https://my.sx.org/",
                "X-Ads-Session": session_id,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }
            req = urllib.request.Request(cf_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("data"):
                    logger.info("[+] [Cloudflare Edge] Successfully fetched proxies via Worker!")
                    return data.get("data", [])
        except Exception as e:
            logger.debug(f"Cloudflare proxy fetch notice: {e}. Falling back...")

    # 2. Direct fallback
    try:
        direct_url = "https://api.sx.org/proxy/short"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://my.sx.org",
            "Referer": "https://my.sx.org/",
            "X-Ads-Session": session_id,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        req = urllib.request.Request(direct_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        logger.error(f"Direct proxy fetch failed: {e}")
        return []

def activate_account_proxies(account_dict: dict) -> bool:
    """Instantly saves proxies to proxy.txt and active vault (<10ms)."""
    proxies = account_dict.get("proxies", [])
    if not proxies:
        return False

    lines = []
    for p in proxies:
        line = format_proxy(p)
        if line and ":" in line:
            lines.append(line)

    if not lines:
        return False

    try:
        with open(PROXY_TXT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(PROXIES_TXT_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        logger.info(f"[+] [ACTIVATED] {len(lines)} proxies active in proxy.txt!")
        return True
    except Exception as e:
        logger.error(f"Error activating proxies: {e}")
        return False

def remove_dead_proxy(dead_proxy_str: str) -> bool:
    """
    HOT-SWAP ENGINE: Immediately purges a dead 407 / failed proxy in <0.001s
    and saves remaining live proxies so that traffic never drops!
    """
    if not os.path.exists(PROXY_TXT_FILE):
        return False

    dead_raw = (dead_proxy_str or "").strip()
    dead_user = ""
    parts = dead_raw.split(":")
    if len(parts) >= 3:
        dead_user = parts[2].split("-session-")[0].split("_session_")[0].strip()

    try:
        with open(PROXY_TXT_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        remaining = []
        removed = 0
        for l in lines:
            matches_raw = (dead_raw and (dead_raw in l or l in dead_raw))
            matches_user = (dead_user and len(dead_user) > 5 and dead_user in l)
            if matches_raw or matches_user:
                removed += 1
            else:
                remaining.append(l)

        if removed > 0:
            with open(PROXY_TXT_FILE, "w", encoding="utf-8") as f:
                if remaining:
                    f.write("\n".join(remaining) + "\n")
                else:
                    f.write("")
            logger.warning(f"[HOT-SWAP] Purged {removed} dead 407 proxy! Live proxies remaining: {len(remaining)}")

            # If all proxies depleted, pop next pre-claimed account from vault
            if not remaining:
                pop_and_activate_next()
            return True
    except Exception as e:
        logger.error(f"Error removing dead proxy: {e}")
    return False

def add_account_to_vault(account_dict: dict):
    vault = _load_vault()
    acc_id = account_dict.get("id") or account_dict.get("email")
    for v in vault:
        if v.get("id") == acc_id or v.get("email") == acc_id:
            return
    vault.append(account_dict)
    _save_vault(vault)
    logger.info(f"[+] [Vault Reserve] Added account {acc_id[:15]} to reserve (Total: {len(vault)})")

    # If proxy.txt is empty, activate immediately
    if not os.path.exists(PROXY_TXT_FILE) or os.path.getsize(PROXY_TXT_FILE) == 0:
        activate_account_proxies(account_dict)

def pop_and_activate_next() -> bool:
    vault = _load_vault()
    if not vault:
        return False
    next_acc = vault.pop(0)
    _save_vault(vault)
    return activate_account_proxies(next_acc)
