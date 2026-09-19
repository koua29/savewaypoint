"""
SaveWaypoint - Decky Loader plugin backend.

Exposes async methods callable from the React frontend. Handles GitHub device-flow
login, save auto-detection, and backup/restore of selected game saves to a private
GitHub repo (one tar.gz per game, versioned by git history).
"""

import os
import decky  # provided by Decky Loader at runtime

import detector
import github_store as gh
from swp_settings import Settings


class Plugin:
    async def _main(self):
        self.settings = Settings()
        self._device = None  # in-flight device-flow state
        decky.logger.info("SaveWaypoint loaded")

    async def _unload(self):
        decky.logger.info("SaveWaypoint unloaded")

    async def _uninstall(self):
        pass

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
            token = payload
            try:
                user = gh.get_user(token)
            except gh.GitHubError as e:
                return {"state": "error", "error": str(e)}
            self.settings.set("token", token)
            self.settings.set("login", user["login"])
            self._device = None
            return {"state": "ok", "login": user["login"]}
        return {"state": state}

    async def disconnect(self):
        self.settings.clear_auth()
        return {"ok": True}

    # --- Detection ---------------------------------------------------------
    async def scan(self):
        entries = detector.full_scan(self.settings.get("watched"))
        selected = set(self.settings.get("selected"))
        for e in entries:
            e["selected"] = e["id"] in selected
        return {"ok": True, "entries": entries}

    async def set_selection(self, ids):
        # Persist selection + remember each selected entry's path for restore.
        self.settings.set("selected", list(ids))
        entries = detector.full_scan(self.settings.get("watched"))
        by_id = {e["id"]: e for e in entries}
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
        return {"ok": True}

    async def remove_watched(self, path: str):
        watched = [w for w in self.settings.get("watched") if w != path]
        self.settings.set("watched", watched)
        return {"ok": True}

    # --- Backup / restore --------------------------------------------------
    async def _repo(self):
        token = self.settings.get("token")
        owner = self.settings.get("login")
        repo = self.settings.get("repo")
        gh.ensure_repo(token, owner, repo, private=True)
        return token, owner, repo

    async def backup(self, ids=None):
        s = self.settings
        if not s.is_connected():
            return {"ok": False, "error": "not connected"}
        ids = ids if ids is not None else s.get("selected")
        if not ids:
            return {"ok": False, "error": "nothing selected"}
        try:
            token, owner, repo = await self._repo()
        except gh.GitHubError as e:
            return {"ok": False, "error": str(e)}

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
                meta = f'{{"name": {info["name"]!r}, "path": {info["path"]!r}, "source": {info.get("source","")!r}}}'
                gh.put_file(token, owner, repo, f"saves/{eid}/meta.json",
                            meta.encode("utf-8"), f"meta: {info['name']}")
                results.append({"id": eid, "ok": True, "name": info["name"],
                                "size": len(blob)})
            except gh.GitHubError as e:
                results.append({"id": eid, "ok": False, "error": str(e)})
        ok = sum(1 for r in results if r["ok"])
        return {"ok": True, "count": ok, "total": len(results), "results": results}

    async def restore(self, entry_id: str, ref: str = None):
        s = self.settings
        if not s.is_connected():
            return {"ok": False, "error": "not connected"}
        token, owner, repo = s.get("token"), s.get("login"), s.get("repo")
        try:
            raw = gh.get_file(token, owner, repo, f"saves/{entry_id}/backup.tar.gz", ref=ref)
        except gh.GitHubError as e:
            return {"ok": False, "error": str(e)}
        if raw is None:
            return {"ok": False, "error": "no backup in cloud for this game"}
        info = s.get("paths").get(entry_id)
        dest = info["path"] if info else None
        if not dest:
            # Fall back to meta.json stored in the repo.
            try:
                meta_raw = gh.get_file(token, owner, repo, f"saves/{entry_id}/meta.json", ref=ref)
                import json as _json
                dest = _json.loads(meta_raw.decode("utf-8"))["path"]
            except Exception:
                return {"ok": False, "error": "unknown restore path"}
        try:
            gh.extract_archive_bytes(raw, dest)
        except gh.GitHubError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": dest}

    async def history(self, entry_id: str):
        s = self.settings
        if not s.is_connected():
            return {"ok": False, "error": "not connected"}
        commits = gh.file_history(s.get("token"), s.get("login"), s.get("repo"),
                                  f"saves/{entry_id}/backup.tar.gz")
        return {"ok": True, "commits": commits}

    async def test_entry(self, entry_id: str):
        """Dry-run: confirm the save can be read & archived, and restore target
        is writable. Powers the UI 'Test this save' button."""
        info = self.settings.get("paths").get(entry_id)
        if not info:
            entries = detector.full_scan(self.settings.get("watched"))
            info = next((e for e in entries if e["id"] == entry_id), None)
        if not info:
            return {"ok": False, "error": "entry not found"}
        path = info["path"]
        if not os.path.isdir(path):
            return {"ok": False, "error": "folder missing"}
        try:
            blob = gh.make_archive_bytes(path)
        except Exception as e:
            return {"ok": False, "error": f"cannot archive: {e}"}
        writable = os.access(path, os.W_OK)
        return {"ok": True, "readable": True, "restorable": writable,
                "archive_size": len(blob)}

    async def set_auto_backup(self, enabled: bool):
        self.settings.set("auto_backup", bool(enabled))
        return {"ok": True}
