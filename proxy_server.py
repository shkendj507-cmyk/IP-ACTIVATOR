import asyncio
import base64
import logging
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import proxy_vault

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [VPS-Proxy] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("VPSProxy")

BUFFER_SIZE = 65536
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_TXT_FILE = os.path.join(BASE_DIR, "proxy.txt")

ALL_COUNTRIES = {
    "AF": "Afghanistan", "AL": "Albania", "DZ": "Algeria", "AR": "Argentina", "AU": "Australia",
    "BD": "Bangladesh", "BE": "Belgium", "BJ": "Benin", "BR": "Brazil", "CA": "Canada",
    "CN": "China", "DE": "Germany", "ES": "Spain", "FR": "France", "GB": "United Kingdom",
    "HK": "Hong Kong", "IN": "India", "IT": "Italy", "JP": "Japan", "NL": "Netherlands",
    "RU": "Russia", "SG": "Singapore", "TR": "Turkey", "UA": "Ukraine", "US": "United States"
}

COUNTRY_PORTS = {
    9001: ("US", "United States"),
    9002: ("GB", "United Kingdom"),
    9003: ("DE", "Germany"),
    9004: ("CA", "Canada"),
    9006: ("AU", "Australia"),
    9007: ("IN", "India"),
    9008: ("SG", "Singapore"),
    9009: ("JP", "Japan"),
}

def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"

def get_tailscale_ip() -> str:
    try:
        out = subprocess.check_output(["tailscale", "ip", "-4"], timeout=2).decode("utf-8").strip()
        lines = out.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("100."):
                return line
    except Exception:
        pass
    return ""

def optimize_socket(writer):
    if not writer:
        return
    try:
        sock = writer.get_extra_info('socket')
        if sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
                if hasattr(socket, 'TCP_QUICKACK'):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
                if hasattr(socket, 'IP_TOS'):
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0x10)
            except Exception:
                pass
    except Exception:
        pass

_DNS_CACHE = {}
async def get_cached_ip(host):
    now = time.time()
    if host in _DNS_CACHE:
        ip, exp = _DNS_CACHE[host]
        if now < exp:
            return ip
    try:
        loop = asyncio.get_running_loop()
        res = await loop.getaddrinfo(host, None, family=socket.AF_INET)
        if res:
            ip = res[0][4][0]
            _DNS_CACHE[host] = (ip, now + 1800)
            return ip
    except Exception:
        pass
    return host

class FastProxyServer:
    def __init__(self, host="0.0.0.0", default_country="US"):
        self.host = host
        self.lan_ip = get_lan_ip()
        self.tailscale_ip = get_tailscale_ip()
        self.default_country = default_country.upper()
        self.proxies_cache = []
        self.last_mtime = 0
        self.proxy_sessions = {}
        self._rotation_lock = asyncio.Lock()

    def load_proxies(self, force=False):
        if not os.path.exists(PROXY_TXT_FILE):
            self.proxies_cache = []
            return self.proxies_cache

        try:
            mtime = os.path.getmtime(PROXY_TXT_FILE)
            if not force and mtime == self.last_mtime and self.proxies_cache:
                return self.proxies_cache

            parsed = []
            with open(PROXY_TXT_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(":")
                    if len(parts) >= 2:
                        parsed.append({
                            "host": parts[0].strip(),
                            "port": int(parts[1].strip()),
                            "user": parts[2].strip() if len(parts) >= 3 else "",
                            "pass": parts[3].strip() if len(parts) >= 4 else "",
                            "raw": line
                        })
            self.proxies_cache = parsed
            self.last_mtime = mtime
            logger.info(f"Loaded {len(parsed)} upstream proxies from proxy.txt")
        except Exception as e:
            logger.error(f"Error loading proxy.txt: {e}")

        return self.proxies_cache

    def get_upstream_proxy(self, requested_country: str = None, exclude_raw: str = None, client_key: str = None):
        proxies = self.load_proxies()
        if not proxies:
            return None

        candidates = [p for p in proxies if p.get("raw") != exclude_raw] if exclude_raw else proxies
        if not candidates:
            candidates = proxies

        req_c = (requested_country or self.default_country).lower()
        matched = []
        for p in candidates:
            u = p["user"].lower()
            if f"-country-{req_c}" in u or f"-cc-{req_c}" in u or f"_{req_c}" in u:
                matched.append(p)

        target_pool = matched if matched else candidates

        if client_key:
            hash_idx = sum(ord(c) for c in client_key) % len(target_pool)
            return target_pool[hash_idx]

        return target_pool[0]

    def parse_client_username(self, raw_user: str, fixed_country: str = None):
        if not raw_user:
            return (fixed_country or self.default_country), None

        raw = raw_user.strip()
        req_session = None
        req_country = None

        m_hold = re.search(r'[-_](?:hold|sticky)', raw, re.IGNORECASE)
        m_rot = re.search(r'[-_](?:rotate|rotating|random)', raw, re.IGNORECASE)
        if m_hold:
            req_session = "hold"
            raw = re.sub(r'[-_](?:hold|sticky)', '', raw, flags=re.IGNORECASE)
        elif m_rot:
            req_session = "rotate"
            raw = re.sub(r'[-_](?:rotate|rotating|random)', '', raw, flags=re.IGNORECASE)
        else:
            m_sess = re.search(r'[-_](?:session|sess)[-_]([a-zA-Z0-9]+)$', raw, re.IGNORECASE)
            if m_sess:
                req_session = m_sess.group(1)
                raw = raw[:m_sess.start()]

        m = re.search(r'(?:^|[-_])(?:country|cc)[-_]([a-zA-Z]{2,3})(?:[-_]|$)', raw, re.IGNORECASE)
        if m:
            req_country = m.group(1).upper()
        elif re.match(r'^[a-zA-Z]{2}$', raw):
            req_country = raw.upper()

        final_country = req_country or fixed_country or self.default_country
        return final_country, req_session

    def format_upstream_user(self, base_user: str, country: str, custom_session: str = None) -> str:
        if not base_user:
            return base_user

        u = re.sub(r'[-_]pool[-_]res\.mobile\.corp', '-pool-res', base_user, flags=re.IGNORECASE)
        c = (country or self.default_country).strip().upper()

        is_hold = custom_session in ("hold", "sticky")
        if custom_session == "rotate":
            sess = uuid.uuid4().hex[:8]
        elif custom_session and not is_hold:
            sess = custom_session
        elif is_hold:
            sess = None
        else:
            sess = self.proxy_sessions.get(base_user)
            if not sess:
                sess = uuid.uuid4().hex[:8]
                self.proxy_sessions[base_user] = sess

        if sess:
            if re.search(r'-session-[a-zA-Z0-9_]+', u, re.IGNORECASE):
                u = re.sub(r'-session-[a-zA-Z0-9_]+', f'-session-{sess}', u, flags=re.IGNORECASE)
            else:
                u = f"{u}-session-{sess}"

        if c and (len(c) == 2 or c in ALL_COUNTRIES):
            if re.search(r'-country-[A-Za-z]+', u, re.IGNORECASE):
                u = re.sub(r'-country-[A-Za-z]+', f'-country-{c}', u, flags=re.IGNORECASE)
            elif not re.search(rf'[-_](?:country|cc)[-_]{c}', u, re.IGNORECASE):
                u = f"{u}-country-{c}"

        return u

    async def dial_upstream_tunnel(self, upstream: dict, formatted_user: str, target_host: str, target_port: int):
        upstream_ip = await get_cached_ip(upstream["host"])
        cred = base64.b64encode(f"{formatted_user}:{upstream['pass']}".encode("utf-8")).decode("utf-8")

        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(upstream_ip, upstream["port"]), timeout=3.5)
            optimize_socket(w)
        except Exception as e:
            return None, None, f"connect_failed: {e}"

        up_req = (
            f"CONNECT {target_host}:{target_port} HTTP/1.1\r\n"
            f"Host: {target_host}:{target_port}\r\n"
            f"Proxy-Authorization: Basic {cred}\r\n"
            f"User-Agent: Mozilla/5.0\r\n\r\n"
        ).encode("utf-8")
        w.write(up_req)
        await w.drain()

        try:
            resp = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), timeout=3.5)
            if resp.startswith(b"HTTP/1.1 200") or resp.startswith(b"HTTP/1.0 200"):
                return r, w, "ok"
            first_line = resp.split(b"\r\n")[0].decode('latin-1', errors='ignore')
            w.close()
            return None, None, first_line
        except Exception as e:
            try: w.close()
            except Exception: pass
            return None, None, f"error: {e}"

    async def connect_with_retry_and_auto_purge(self, req_country: str, client_session: str, target_host: str, target_port: int, max_retries: int = 5):
        """
        Connects to upstream proxy.
        If a proxy returns 407 (Proxy Authentication Required / expired quota),
        it INSTANTLY purges it from proxy.txt in <0.001s, hot-swaps to next proxy, and retries!
        """
        exclude = None
        for attempt in range(max_retries):
            upstream = self.get_upstream_proxy(req_country, exclude_raw=exclude, client_key=client_session or req_country)
            if not upstream:
                break
            formatted_user = self.format_upstream_user(upstream["user"], req_country, client_session)
            r, w, res = await self.dial_upstream_tunnel(upstream, formatted_user, target_host, target_port)
            if res == "ok" and w:
                return r, w, "ok", upstream

            exclude = upstream.get("raw")
            if "407" in str(res):
                logger.warning(f"[407 DETECTED] Upstream {upstream.get('host')} expired. Auto-purging in <0.001s...")
                proxy_vault.remove_dead_proxy(upstream.get("raw"))
                self.load_proxies(force=True)
                continue

            if any(err in str(res).lower() for err in ["timeout", "reset", "failed", "502", "503"]):
                logger.debug(f"Upstream dial retry ({res})...")
                continue

            break

        return None, None, "failed", None

    async def pipe_streams(self, client_reader, client_writer, upstream_reader, upstream_writer):
        async def fwd(src, dst):
            try:
                while True:
                    data = await src.read(BUFFER_SIZE)
                    if not data:
                        break
                    dst.write(data)
                    transport = dst.transport
                    if transport and transport.get_write_buffer_size() > 65536:
                        await dst.drain()
            except Exception:
                pass

        t1 = asyncio.create_task(fwd(client_reader, upstream_writer))
        t2 = asyncio.create_task(fwd(upstream_reader, client_writer))
        await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
        t1.cancel()
        t2.cancel()

    async def handle_client(self, client_reader, client_writer, fixed_country=None):
        optimize_socket(client_writer)
        upstream_writer = None
        try:
            req_data = await client_reader.readuntil(b"\r\n\r\n")
            header_text = req_data.decode("latin-1", errors="replace")
            lines = header_text.split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) < 3:
                client_writer.close()
                return

            method, target_uri = parts[0], parts[1]
            headers = {}
            for line in lines[1:]:
                if ": " in line:
                    k, v = line.split(": ", 1)
                    headers[k.lower()] = v.strip()

            raw_user = ""
            auth = headers.get("proxy-authorization")
            if auth and auth.startswith("Basic "):
                try:
                    cred = base64.b64decode(auth[6:].strip()).decode("utf-8")
                    raw_user = cred.split(":", 1)[0]
                except Exception:
                    pass

            req_country, client_session = self.parse_client_username(raw_user, fixed_country)

            if method.upper() == "CONNECT":
                if ":" in target_uri:
                    target_host, target_port = target_uri.split(":", 1)
                    target_port = int(target_port)
                else:
                    target_host, target_port = target_uri, 443
            else:
                parsed = urlparse(target_uri)
                target_host = parsed.hostname or headers.get("host", "").split(":")[0]
                target_port = parsed.port or (443 if parsed.scheme == "https" else 80)

            r, w, res, used_up = await self.connect_with_retry_and_auto_purge(
                req_country, client_session, target_host, target_port
            )
            if res != "ok" or not w:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await client_writer.drain()
                client_writer.close()
                return

            upstream_writer = w
            if method.upper() == "CONNECT":
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
            else:
                # Forward plain HTTP request
                cred = base64.b64encode(f"{self.format_upstream_user(used_up['user'], req_country, client_session)}:{used_up['pass']}".encode()).decode()
                clean = [l for l in lines[1:] if not l.lower().startswith("proxy-authorization:")]
                clean.append(f"Proxy-Authorization: Basic {cred}")
                fwd_http = f"{parts[0]} {target_uri} {parts[2]}\r\n" + "\r\n".join(clean) + "\r\n\r\n"
                w.write(fwd_http.encode("latin-1"))
                await w.drain()

            await self.pipe_streams(client_reader, client_writer, r, w)

        except Exception:
            pass
        finally:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass
            if upstream_writer:
                try:
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
                except Exception:
                    pass

    async def start(self):
        self.load_proxies(force=True)
        servers = []
        for port in [1000, 8080]:
            srv = await asyncio.start_server(lambda r, w: self.handle_client(r, w), self.host, port)
            servers.append(srv)

        for port, (cc, cname) in COUNTRY_PORTS.items():
            srv = await asyncio.start_server(lambda r, w, c=cc: self.handle_client(r, w, fixed_country=c), self.host, port)
            servers.append(srv)

        print("\n" + "=" * 75)
        print("      EASYADS VPS AUTOMATION PROXY ENGINE (ACTIVE 24/7)")
        print("=" * 75)
        print(f" [+] Primary Ports    : 1000 & 8080 (Any Country / Auto-Routing)")
        if self.tailscale_ip:
            print(f" [+] Tailscale IP     : {self.tailscale_ip}:1000 (Global WireGuard Direct)")
        if self.lan_ip:
            print(f" [+] Local LAN IP     : {self.lan_ip}:1000 (Wi-Fi / Local Machine)")
        print(f" [+] 407 Auto-Purge   : Active (<0.001s Instant Hot-Swap)")
        print(f" [+] Sessions         : -rotate (Auto New IP) | -hold (Sticky) | -session-1")
        print("=" * 75 + "\n")

        await asyncio.gather(*[s.serve_forever() for s in servers])
