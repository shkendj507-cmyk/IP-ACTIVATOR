import requests
import time

# Options:
# 1. Auto-Rotate New IP:   manik-country-BJ-rotate:easyads
# 2. Custom Profile IP:     manik-country-BJ-session-1:easyads
# 3. Permanent Sticky IP:   manik-country-BJ-hold:easyads
PROXY = "127.0.0.1:1000:manik-country-BJ-rotate:easyads"

host, port, username, password = PROXY.split(":", 3)
proxy_url = f"http://{username}:{password}@{host}:{port}"
proxies = {"http": proxy_url, "https": proxy_url}

session = requests.Session()
session.proxies = proxies

print(f"[*] Testing Proxy: {PROXY}")
print("-" * 65)

while True:
    start = time.perf_counter()
    try:
        response = session.get(
            "http://ip-api.com/json/?fields=status,country,countryCode,query",
            timeout=8
        )
        elapsed = (time.perf_counter() - start) * 1000
        data = response.json()
        if response.ok and data.get("status") == "success":
            print(f"IP: {data.get('query')} | Country: {data.get('country')} ({data.get('countryCode')}) | Response: {elapsed:.0f} ms")
        else:
            print(f"Proxy error: {data}")
    except requests.exceptions.RequestException as e:
        print(f"FAILED: {e}")

    time.sleep(1)
