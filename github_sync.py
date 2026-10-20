import base64
import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error

logger = logging.getLogger("GitHubSync")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config.json: {e}")
    return {}

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving config.json: {e}")

_push_lock = threading.Lock()

class GitHubBridge:
    """
    Direct GitHub REST API Bridge.
    Syncs AdsPower accounts between PC and VPS automatically without needing git CLI.
    """
    def __init__(self, token: str = None, repo: str = None, file_path: str = "accounts.json", branch: str = "main"):
        cfg = load_config().get("github", {})
        self.token = (token or cfg.get("token") or os.environ.get("GITHUB_TOKEN", "")).strip()
        self.repo = (repo or cfg.get("repo") or os.environ.get("GITHUB_REPOSITORY", "")).strip()
        self.file_path = file_path or cfg.get("file_path", "accounts.json")
        self.branch = branch or cfg.get("branch", "main")

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "EasyAds-VPS-Automation"
        }
        if self.token and (self.token.startswith("ghp_") or self.token.startswith("github_pat_")):
            headers["Authorization"] = f"token {self.token}"
        return headers

    def get_remote_accounts(self) -> list:
        """Pulls the latest raw accounts list from GitHub with zero-401 resilience."""
        if not self.repo or "/" not in self.repo:
            return []

        raw_url = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{self.file_path}"
        try:
            req = urllib.request.Request(raw_url, headers={"User-Agent": "EasyAds-VPS"}, method="GET")
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:
            pass

        url = f"https://api.github.com/repos/{self.repo}/contents/{self.file_path}?ref={self.branch}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content_b64 = data.get("content", "")
                decoded = base64.b64decode(content_b64).decode("utf-8")
                parsed = json.loads(decoded)
                return parsed if isinstance(parsed, list) else []
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            logger.debug(f"GitHub fetch HTTP {e.code}: {e.reason}")
        except Exception as e:
            logger.debug(f"GitHub fetch error: {e}")
        return []

    def push_account(self, new_account: dict) -> bool:
        """Appends a newly created AdsPower account on PC and pushes to GitHub with conflict resolution."""
        if not self.repo or "/" not in self.repo or not self.token or self.token.startswith("ghp_YOUR"):
            logger.warning("[PC-Sync] GitHub token/repo not configured in config.json! Saving to local accounts.json...")
            local_file = os.path.join(BASE_DIR, "accounts.json")
            existing = []
            if os.path.exists(local_file):
                try:
                    with open(local_file, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    pass
            existing.append(new_account)
            with open(local_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
            return True

        url = f"https://api.github.com/repos/{self.repo}/contents/{self.file_path}"
        new_id = new_account.get("email") or new_account.get("auth_code") or new_account.get("token") or "acc"

        with _push_lock:
            for attempt in range(1, 6):
                current_sha = None
                existing_accounts = []

                # 1. Fetch current remote state
                try:
                    get_req = urllib.request.Request(f"{url}?ref={self.branch}", headers=self._headers(), method="GET")
                    with urllib.request.urlopen(get_req, timeout=10) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        current_sha = data.get("sha")
                        content = base64.b64decode(data.get("content", "")).decode("utf-8")
                        existing_accounts = json.loads(content)
                        if not isinstance(existing_accounts, list):
                            existing_accounts = []
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        logger.error(f"GitHub check error: {e}")
                        time.sleep(1)
                        continue
                except Exception:
                    pass

                # 2. Append new account (avoid duplicates)
                duplicate = False
                for acc in existing_accounts:
                    if (acc.get("email") and acc.get("email") == new_id) or (acc.get("auth_code") and acc.get("auth_code") == new_id):
                        duplicate = True
                        break

                if duplicate:
                    logger.info(f"Account {new_id} already exists on GitHub.")
                    return True

                existing_accounts.append(new_account)
                new_content_bytes = json.dumps(existing_accounts, indent=2).encode("utf-8")
                encoded_content = base64.b64encode(new_content_bytes).decode("utf-8")

                # 3. Commit to GitHub
                payload = {
                    "message": f"Auto-Sync: Add AdsPower Account {new_id[:15]} [skip ci]",
                    "content": encoded_content,
                    "branch": self.branch
                }
                if current_sha:
                    payload["sha"] = current_sha

                put_data = json.dumps(payload).encode("utf-8")
                put_req = urllib.request.Request(url, data=put_data, headers=self._headers(), method="PUT")

                try:
                    with urllib.request.urlopen(put_req, timeout=12) as resp:
                        if resp.status in (200, 201):
                            logger.info(f"[+] [GitHub Auto-Sync] Successfully pushed AdsPower account to GitHub repo ({self.repo})!")
                            return True
                except urllib.error.HTTPError as e:
                    if e.code == 409:
                        # Conflict: another thread committed just now. Wait briefly and retry!
                        time.sleep(0.8 + attempt * 0.4)
                        continue
                    logger.error(f"[!] GitHub push failed (HTTP {e.code}): {e.reason}")
                    return False
                except Exception as e:
                    logger.error(f"[!] GitHub push failed: {e}")
                    time.sleep(1)

        return False
