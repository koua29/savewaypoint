"""
SaveWaypoint - GitHub storage backend.

Stores each selected game's save folder as a single tar.gz blob inside a PRIVATE
repo. Git history gives free versioning ("restore a previous version").

Layout in the repo:
    saves/<entry_id>/backup.tar.gz     # the archived save folder
    saves/<entry_id>/meta.json         # name, original absolute path, stats

Authentication uses GitHub's OAuth "device flow": the Deck shows a short code,
the user approves it on their phone at https://github.com/login/device. No
browser or keyboard needed on the handheld, and NO app-verification wall (unlike
Google Drive). A Personal Access Token can also be supplied directly (advanced).

Only the Python standard library is used (urllib), so there is nothing to install
on the Deck.
"""

import io
import os
import json
import time
import base64
import tarfile
import urllib.request
import urllib.error

API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
USER_AGENT = "SaveWaypoint/0.1"


class GitHubError(Exception):
    pass


def _request(method, url, token=None, data=None, accept="application/json"):
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {"message": raw}
        return e.code, parsed
    except urllib.error.URLError as e:
        raise GitHubError(f"network error: {e}")


def _form_request(url, fields):
    """POST application/x-www-form-urlencoded, expect JSON back (device flow)."""
    import urllib.parse
    body = urllib.parse.urlencode(fields).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        raise GitHubError(f"network error: {e}")


# --- Device flow -------------------------------------------------------------

def device_start(client_id, scope="repo"):
    r = _form_request(DEVICE_CODE_URL, {"client_id": client_id, "scope": scope})
    if "device_code" not in r:
        raise GitHubError(r.get("error_description") or r.get("error") or "device_code failed")
    return r  # device_code, user_code, verification_uri, interval, expires_in


def device_poll(client_id, device_code):
    """Poll once. Returns ('pending'|'slow_down'|'ok'|'error', payload)."""
    r = _form_request(TOKEN_URL, {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": DEVICE_GRANT,
    })
    if "access_token" in r:
        return "ok", r["access_token"]
    err = r.get("error")
    if err in ("authorization_pending",):
        return "pending", None
    if err == "slow_down":
        return "slow_down", None
    return "error", r.get("error_description") or err or "unknown error"


# --- Account / repo ----------------------------------------------------------

def get_user(token):
    status, data = _request("GET", f"{API}/user", token=token)
    if status != 200:
        raise GitHubError(data.get("message", f"HTTP {status}"))
    return data  # login, ...


def ensure_repo(token, owner, repo, private=True):
    status, data = _request("GET", f"{API}/repos/{owner}/{repo}", token=token)
    if status == 200:
        return data
    if status != 404:
        raise GitHubError(data.get("message", f"HTTP {status}"))
    status, data = _request("POST", f"{API}/user/repos", token=token, data={
        "name": repo,
        "private": bool(private),
        "auto_init": True,
        "description": "SaveWaypoint game-save backups (private).",
    })
    if status not in (200, 201):
        raise GitHubError(data.get("message", f"HTTP {status}"))
    # small delay so auto_init commit lands before first content push
    time.sleep(1.5)
    return data


# --- Archive helpers ---------------------------------------------------------

def make_archive_bytes(folder):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(folder, arcname=".")
    return buf.getvalue()


def extract_archive_bytes(raw, dest_folder):
    os.makedirs(dest_folder, exist_ok=True)
    buf = io.BytesIO(raw)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        _safe_extract(tar, dest_folder)


def _safe_extract(tar, dest):
    dest_abs = os.path.abspath(dest)
    for member in tar.getmembers():
        target = os.path.abspath(os.path.join(dest, member.name))
        if not target.startswith(dest_abs + os.sep) and target != dest_abs:
            raise GitHubError(f"unsafe path in archive: {member.name}")
    tar.extractall(dest)


# --- Contents API (upload / download) ---------------------------------------

def _get_sha(token, owner, repo, path):
    status, data = _request("GET", f"{API}/repos/{owner}/{repo}/contents/{path}", token=token)
    if status == 200 and isinstance(data, dict):
        return data.get("sha")
    return None


def put_file(token, owner, repo, path, content_bytes, message):
    sha = _get_sha(token, owner, repo, path)
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    status, data = _request("PUT", f"{API}/repos/{owner}/{repo}/contents/{path}",
                            token=token, data=payload)
    if status not in (200, 201):
        raise GitHubError(data.get("message", f"HTTP {status} on PUT {path}"))
    return data


def get_file(token, owner, repo, path, ref=None):
    url = f"{API}/repos/{owner}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    status, data = _request("GET", url, token=token)
    if status == 404:
        return None
    if status != 200:
        raise GitHubError(data.get("message", f"HTTP {status}"))
    if data.get("encoding") == "base64" and data.get("content"):
        return base64.b64decode(data["content"])
    # Large files (>1MB) come without inline content -> use download_url.
    dl = data.get("download_url")
    if dl:
        req = urllib.request.Request(dl, headers={
            "Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    return None


def list_remote_entries(token, owner, repo):
    status, data = _request("GET", f"{API}/repos/{owner}/{repo}/contents/saves", token=token)
    if status == 404:
        return []
    if status != 200:
        raise GitHubError(data.get("message", f"HTTP {status}"))
    return [d["name"] for d in data if d.get("type") == "dir"]


def file_history(token, owner, repo, path, limit=10):
    """Commit list touching a path -> version history for the UI."""
    url = f"{API}/repos/{owner}/{repo}/commits?path={path}&per_page={limit}"
    status, data = _request("GET", url, token=token)
    if status != 200:
        return []
    out = []
    for c in data:
        out.append({
            "sha": c["sha"],
            "date": c["commit"]["committer"]["date"],
            "message": c["commit"]["message"],
        })
    return out
