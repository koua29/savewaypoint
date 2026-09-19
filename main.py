"""
SaveWaypoint - Decky Loader plugin backend.

Exposes async methods callable from the React frontend. Handles GitHub device-flow
login, save auto-detection, and backup/restore of selected game saves to a private
GitHub repo (one tar.gz per game, versioned by git history).
"""

import os
import json
import time
import asyncio

import decky  # provided by Decky Loader at runtime

import detector
import github_store as gh
import updater
from swp_settings import Settings

SCAN_TTL = 20.0  # seconds a scan result stays usable (toggling must not rescan)
CURRENT_VERSION = getattr(decky, "DECKY_PLUGIN_VERSION", "0.0.0")


def _target_ids():
    """uid/gid of the Steam user, so files restored while running as root stay
    writable by the game."""
    if os.geteuid() != 0:
        return None, None
    user = os.environ.get("DECKY_USER")
    if not user:
        return None, None
    try:
        import pwd
        pw = pwd.getpwnam(user)
        return pw.pw_uid, pw.pw_gid
    except Exception:
        return None, None


class Plugin:
    async def _main(self):
        self.settings = Settings()
        self._device = None        # in-flight device-flow state
        self._scan_cache = None    # (timestamp, entries)
        self._update = None        # last update-check result
        self._update_checked = 0.0
        decky.logger.info("SaveWaypoint %s loaded", CURRENT_VERSION)

    async def _unload(self):
        decky.logger.info("SaveWaypoint unloaded")

    async def _uninstall(self):
        pass

    # --- internals ---------------------------------------------------------
    def _entries(self, force=False):
        """Cached scan. A full scan walks every Proton prefix, so it must not run
        again just because the user ticked a checkbox."""
        now = time.time()
        if not force and self._scan_cache and now - self._scan_cache[0] < SCAN_TTL:
            return self._scan_cache[1]
        entries = detector.full_scan(self.settings.get("watched"))
        self._scan_cache = (now, entries)
        return entries

    # --- Status ------------------------------------------------------------
    async def get_status(self):
        s = self.settings
        return {
            "connected": s.is_connected(),
            "login": s.get("login"),
            "repo": s.get("repo"),
            "has_client_id": bool(s.get("client_id")),
            "selected": s.get("selected"),
            "auto_backup": s.get("auto_backup"),
            "watched": s.get("watched"),
        }

    async def set_client_id(self, client_id: str):
        self.settings.set("client_id", (client_id or "").strip())
        return {"ok": True}

    async def set_pat(self, token: str):
        """Advanced: use a Personal Access Token directly (skip device flow)."""
        token = (token or "").strip()
        if not token:
            return {"ok": False, "error": "empty token"}
        try:
            user = gh.get_user(token)
        except gh.GitHubError as e:
            return {"ok": False, "error": str(e)}
        self.settings.set("token", token)
        self.settings.set("login", user["login"])
        return {"ok": True, "login": user["login"]}

    # --- Device-flow login -------------------------------------------------
    async def login_start(self):
        client_id = self.settings.get("client_id")
        if not client_id:
            return {"ok": False, "error": "no client_id set"}
        try:
            d = gh.device_start(client_id)
        except gh.GitHubError as e:
            return {"ok": False, "error": str(e)}
        self._device = {"device_code": d["device_code"], "interval": d.get("interval", 5)}
        return {
            "ok": True,
            "user_code": d["user_code"],
            "verification_uri": d["verification_uri"],
            "expires_in": d.get("expires_in", 900),
            "interval": d.get("interval", 5),
        }

    async def login_poll(self):
        if not self._device:
            return {"state": "error", "error": "no login in progress"}
        client_id = self.settings.get("client_id")
        try:
            state, payload = gh.device_poll(client_id, self._device["device_code"])
        except gh.GitHubError as e:
            return {"state": "error", "error": str(e)}
        if state == "ok":
            try:
                user = gh.get_user(payload)
            except gh.GitHubError as e:
                return {"state": "error", "error": str(e)}
            self.settings.set("token", payload)
            self.settings.set("login", user["login"])
            self._device = None
            return {"state": "ok", "login": user["login"]}
        if state in ("expired", "denied", "error"):
            self._device = None
            return {"state": "error", "error": payload}
        return {"state": state}

    async def login_cancel(self):
        self._device = None
        return {"ok": True}

    async def disconnect(self):
        self.settings.clear_auth()
        return {"ok": True}

    # --- Detection ---------------------------------------------------------
    async def scan(self, force: bool = True):
        try:
            entries = self._entries(force=force)
        except Exception as e:
            decky.logger.exception("scan failed")
            return {"ok": False, "error": str(e)}
        selected = set(self.settings.get("selected"))
        out = [dict(e, selected=e["id"] in selected) for e in entries]
        return {"ok": True, "entries": out}

    async def set_selection(self, ids):
        ids = list(ids or [])
        self.settings.set("selected", ids)
        by_id = {e["id"]: e for e in self._entries()}
        for eid in ids:
            if eid in by_id:
                self.settings.remember_path(by_id[eid])
        self.settings.save()
        return {"ok": True}

    async def add_watched(self, path: str):
        path = os.path.expanduser((path or "").strip())
        if not os.path.isdir(path):
            return {"ok": False, "error": "folder not found"}
        watched = self.settings.get("watched")
        if path not in watched:
            watched.append(path)
            self.settings.set("watched", watched)
            self._scan_cache = None
        return {"ok": True}

    async def remove_watched(self, path: str):
        self.settings.set("watched",
                          [w for w in self.settings.get("watched") if w != path])
        self._scan_cache = None
        return {"ok": True}

    # --- Backup / restore --------------------------------------------------
    async def backup(self, ids=None):
        s = self.settings
        if not s.is_connected():
            return {"ok": False, "error": "not connected"}
        ids = list(ids) if ids else s.get("selected")
        if not ids:
            return {"ok": False, "error": "nothing selected"}
        try:
            gh.ensure_repo(s.get("token"), s.get("login"), s.get("repo"), private=True)
        except gh.GitHubError as e:
            return {"ok": False, "error": str(e)}

        token, owner, repo = s.get("token"), s.get("login"), s.get("repo")
        results = []
        paths = s.get("paths")
        for eid in ids:
            info = paths.get(eid)
            if not info or not os.path.isdir(info["path"]):
                results.append({"id": eid, "ok": False, "error": "path missing"})
                continue
            try:
                blob = gh.make_archive_bytes(info["path"])
                gh.put_file(token, owner, repo, f"saves/{eid}/backup.tar.gz",
                            blob, f"backup: {info['name']}")
                meta = json.dumps({
                    "name": info["name"],
                    "path": info["path"],
                    "source": info.get("source", ""),
                    "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }, indent=2).encode("utf-8")
                gh.put_file(token, owner, repo, f"saves/{eid}/meta.json",
                            meta, f"meta: {info['name']}")
                results.append({"id": eid, "ok": True, "name": info["name"],
                                "size": len(blob)})
            except gh.GitHubError as e:
                results.append({"id": eid, "ok": False,
                                "name": info["name"], "error": str(e)})
            except OSError as e:
                results.append({"id": eid, "ok": False,
                                "name": info["name"], "error": f"read error: {e}"})
        ok = sum(1 for r in results if r["ok"])
        return {"ok": True, "count": ok, "total": len(results), "results": results}

    async def restore(self, entry_id: str, ref: str = None):
        s = self.settings
        if not s.is_connected():
            return {"ok": False, "error": "not connected"}
        token, owner, repo = s.get("token"), s.get("login"), s.get("repo")
        try:
            raw = gh.get_file(token, owner, repo,
                              f"saves/{entry_id}/backup.tar.gz", ref=ref)
        except gh.GitHubError as e:
            return {"ok": False, "error": str(e)}
        if raw is None:
            return {"ok": False, "error": "no backup in the cloud for this game"}

        info = s.get("paths").get(entry_id)
        dest = info["path"] if info else None
        if not dest:
            # Restoring on a fresh device: the repo's meta.json knows the path.
            try:
                meta_raw = gh.get_file(token, owner, repo,
                                       f"saves/{entry_id}/meta.json", ref=ref)
                dest = json.loads(meta_raw.decode("utf-8"))["path"]
            except Exception:
                return {"ok": False, "error": "unknown restore path"}
        try:
            gh.extract_archive_bytes(raw, dest)
            gh.chown_tree(dest, *_target_ids())
        except (gh.GitHubError, OSError) as e:
            return {"ok": False, "error": str(e)}
        self._scan_cache = None
        return {"ok": True, "path": dest}

    async def history(self, entry_id: str):
        s = self.settings
        if not s.is_connected():
            return {"ok": False, "error": "not connected"}
        commits = gh.file_history(s.get("token"), s.get("login"), s.get("repo"),
                                  f"saves/{entry_id}/backup.tar.gz")
        return {"ok": True, "commits": commits}

    async def test_entry(self, entry_id: str):
        """Dry-run: confirm the save can be read and archived, and that its folder
        is writable so a restore would succeed. Powers the UI 'Test' button."""
        info = self.settings.get("paths").get(entry_id)
        if not info:
            info = next((e for e in self._entries() if e["id"] == entry_id), None)
        if not info:
            return {"ok": False, "error": "entry not found"}
        path = info["path"]
        if not os.path.isdir(path):
            return {"ok": False, "error": "folder missing"}
        try:
            blob = gh.make_archive_bytes(path)
        except (gh.GitHubError, OSError) as e:
            return {"ok": False, "error": f"cannot archive: {e}"}
        return {
            "ok": True,
            "readable": True,
            "restorable": os.access(path, os.W_OK),
            "archive_size": len(blob),
            "warn_large": len(blob) > gh.SOFT_LIMIT,
        }

    async def set_auto_backup(self, enabled: bool):
        self.settings.set("auto_backup", bool(enabled))
        return {"ok": True}

    # --- Release channel / updates -----------------------------------------
    async def get_version(self):
        return {"version": CURRENT_VERSION, "beta": bool(self.settings.get("beta"))}

    async def set_beta(self, enabled: bool):
        """Switch release channel. Forces the next check to hit GitHub so the
        other channel's build shows up immediately."""
        self.settings.set("beta", bool(enabled))
        self._update = None
        self._update_checked = 0.0
        return {"ok": True}

    async def check_update(self, force: bool = False):
        now = time.time()
        if (not force and self._update is not None
                and now - self._update_checked < updater.UPDATE_INTERVAL):
            return self._update
        result = await asyncio.to_thread(
            updater.latest_release, CURRENT_VERSION, bool(self.settings.get("beta")))
        self._update = result
        self._update_checked = now
        return result
