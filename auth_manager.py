import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

class AuthManager:
    """Manages multiple Divar authentication sessions and token rotation."""

    def __init__(self, data_file: str = "data/accounts.json"):
        self.data_file = data_file
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        self.accounts: List[Dict[str, Any]] = self._load()
        self._current_index = 0

    def _load(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.accounts, f, ensure_ascii=False, indent=2)

    def add_account(self, phone: str, token: str) -> Dict[str, Any]:
        """Adds or updates an account."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for acc in self.accounts:
            if acc["phone"] == phone:
                acc["token"] = token
                acc["status"] = "active"
                acc["updated_at"] = now
                self._save()
                return acc

        account = {
            "phone": phone,
            "token": token,
            "status": "active",  # active, quota_exceeded, invalid
            "created_at": now,
            "updated_at": now,
            "used_today": 0,
            "total_extracted": 0,
            "last_used": None,
            "last_date": datetime.now().strftime("%Y-%m-%d")
        }
        self.accounts.append(account)
        self._save()
        return account

    def remove_account(self, phone: str) -> bool:
        """Removes an account by phone."""
        initial_len = len(self.accounts)
        self.accounts = [a for a in self.accounts if a["phone"] != phone]
        if len(self.accounts) < initial_len:
            self._save()
            return True
        return False

    def get_accounts(self) -> List[Dict[str, Any]]:
        """Returns all accounts with masked tokens for safety."""
        today = datetime.now().strftime("%Y-%m-%d")
        for a in self.accounts:
            if a.get("last_date") != today:
                # Reset daily counter on a new day
                a["used_today"] = 0
                a["last_date"] = today
                if a["status"] == "quota_exceeded":
                    a["status"] = "active"
        self._save()

        sanitized = []
        for a in self.accounts:
            copy_a = dict(a)
            token = copy_a.get("token", "")
            copy_a["token_masked"] = token[:6] + "..." + token[-4:] if len(token) > 10 else "******"
            sanitized.append(copy_a)
        return sanitized

    def get_active_account(self) -> Optional[Dict[str, Any]]:
        """
        Gets next available active account for fetching contact details.
        Rotates among active accounts.
        """
        active_accounts = [a for a in self.accounts if a.get("status") == "active"]
        if not active_accounts:
            return None

        # Round-robin
        self._current_index = self._current_index % len(active_accounts)
        acc = active_accounts[self._current_index]
        self._current_index = (self._current_index + 1) % len(active_accounts)
        return acc

    def record_usage(self, phone: str, success: bool):
        """Records a contact number extraction for an account."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today = datetime.now().strftime("%Y-%m-%d")
        for acc in self.accounts:
            if acc["phone"] == phone:
                if acc.get("last_date") != today:
                    acc["used_today"] = 0
                    acc["last_date"] = today
                if success:
                    acc["used_today"] = acc.get("used_today", 0) + 1
                    acc["total_extracted"] = acc.get("total_extracted", 0) + 1
                acc["last_used"] = now
                self._save()
                break

    def mark_quota_exceeded(self, phone: str):
        """Marks an account as having reached Divar's daily contact quota."""
        for acc in self.accounts:
            if acc["phone"] == phone:
                acc["status"] = "quota_exceeded"
                self._save()
                break

    def reset_status(self, phone: str):
        """Manually resets an account status back to active."""
        for acc in self.accounts:
            if acc["phone"] == phone:
                acc["status"] = "active"
                self._save()
                break
