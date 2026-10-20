import proxy_server

def display():
    ts = proxy_server.get_tailscale_ip()
    lan = proxy_server.get_lan_ip()
    print("\n" + "=" * 70)
    print("      [+] EASYADS VPS AUTOMATION CONNECTION INFORMATION         ")
    print("=" * 70)
    print(f"  [+] TAILSCALE IP      : {ts if ts else 'Tailscale Not Running (Run: tailscale up)'}")
    print(f"  [+] LOCAL LAN IP      : {lan}")
    print("  [+] PROXY PORTS       : 1000 (Primary) & 8080 (Secondary)")
    print("  -------------------------------------------------------------------")
    print("  [*] TAILSCALE GLOBAL FORMAT (Phone / PC / Browser anywhere):")
    print(f"      {ts if ts else '100.x.x.x'}:1000:manik-country-US:easyads")
    print(f"      {ts if ts else '100.x.x.x'}:1000:manik-country-BJ-rotate:easyads (Auto-New IP)")
    print(f"      {ts if ts else '100.x.x.x'}:1000:manik-country-BJ-session-1:easyads (Profile 1)")
    print("  -------------------------------------------------------------------")
    print("  [*] DEDICATED COUNTRY PORTS (Auto US/GB/DE/CA/IN/SG):")
    print("      9001 (US), 9002 (GB), 9003 (DE), 9004 (CA), 9007 (IN), 9008 (SG)")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    display()
