# EasyAds VPS Automation: PC-to-GitHub-to-VPS 24/7 Proxy System

## 🌟 Architecture Overview
```
[User Local PC] ──(AdsPower Creator)──► Pushes accounts to GitHub (accounts.json)
                                                   │
                                                   ▼ (Instant Auto-Sync)
[Remote VPS / Server] ◄────────────── Pulls new accounts via GitHub API
        │
        ├─► Claims 10 Proxies via Cloudflare Edge Worker
        ├─► Instant 407 Auto-Purge (<0.001s Hot-Swap - Zero dropped requests)
        └─► High-Speed Proxy Server (Ports 1000 & 8080)
                │
                ▼ (Zero-Hop Direct WireGuard)
[Client Devices Anywhere] ──► Tailscale IP:1000 (US, BJ, DE, SG, IN, etc.)
```

---

## 🚀 Quick Setup & Usage

### 1. Configure GitHub (`config.json`)
Open `config.json` and set your GitHub token & repo name:
```json
{
  "github": {
    "token": "ghp_your_personal_access_token",
    "repo": "your_username/your_repo_name"
  }
}
```

### 2. On Your Local PC
Double-click:
```bash
start_pc.bat
```
*(Runs `pc_creator.py` - creates AdsPower accounts and automatically pushes them to GitHub).*

### 3. On Your VPS
Double-click:
```bash
start_vps.bat
```
*(Runs `vps_sync_engine.py` - pulls accounts from GitHub, claims proxies, and hosts the ultra-fast proxy server 24/7).*

---

## ⚡ Proxy Connection Formats

| Mode | Format | Description |
|---|---|---|
| **Auto-Rotate (New IP every hit)** | `TAILSCALE_IP:1000:manik-country-BJ-rotate:easyads` | নতুন আইপি প্রতি রিকোয়েস্টে |
| **Sticky Profile (Fixed per Profile)** | `TAILSCALE_IP:1000:manik-country-BJ-session-1:easyads` | প্রোফাইল ১-এর জন্য স্টিকি আইপি |
| **Permanent Hold** | `TAILSCALE_IP:1000:manik-country-BJ-hold:easyads` | সম্পূর্ণ লকড ফিক্সড আইপি |
| **Dedicated Country Ports** | `TAILSCALE_IP:9001` (US), `9002` (GB), `9003` (DE), `9008` (SG) | সরাসরি কান্ট্রি পোর্ট |

---

## 🛡️ Zero-Drop 407 Auto-Purge
- কোনো প্রক্সিতে 407 Authentication Required বা কোটা শেষ হলে সিস্টেম **০.০০১ সেকেন্ডে** স্বয়ংক্রিয়ভাবে সেই প্রক্সিটি রিমুভ করে পরবর্তী লাইভ প্রক্সিতে ট্রাফিক সুইচ করে দেয়।
- কোনো কানেকশন ড্রপ হয় না।
