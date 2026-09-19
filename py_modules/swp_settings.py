"""
SaveWaypoint - tiny JSON settings store.

Persists in the Decky plugin settings dir when available, else the plugin folder.
Holds: github token, client_id, owner/login, repo name, selected entry ids,
watched (custom) folders, and a small map of entry_id -> original path so a
restore on another device knows where to write.
"""

import os
import json

DEFAULTS = {
    "client_id": "",          # GitHub OAuth App client id (device flow)
    "token": "",              # access token or PAT
    "login": "",              # github username (repo owner)
    "repo": "savewaypoint-saves",
    "selected": [],           # entry ids the user chose to sync
    "paths": {},              # entry_id -> {"name","path","source"}
    "watched": [],            # user-added custom folders
    "auto_backup": False,
    "beta": False,            # release channel: beta pre-releases or stable only
}


def _settings_path():
    base = os.environ.get("DECKY_PLUGIN_SETTINGS_DIR") \
        or os.environ.get("DECKY_PLUGIN_RUNTIME_DIR") \
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "savewaypoint.json")


class Settings:
    def __init__(self):
        self.path = _settings_path()
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            merged = dict(DEFAULTS)
            merged.update(stored)
            self.data = merged
        except (OSError, ValueError):
            self.data = dict(DEFAULTS)
        return self.data

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def is_connected(self):
        return bool(self.data.get("token") and self.data.get("login"))

    def remember_path(self, entry):
        self.data["paths"][entry["id"]] = {
            "name": entry["name"],
            "path": entry["path"],
            "source": entry.get("source", ""),
        }

    def clear_auth(self):
        self.data["token"] = ""
        self.data["login"] = ""
        self.save()
