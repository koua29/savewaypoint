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
import ssl
import json
import time
import base64
import tarfile
import urllib.parse
import urllib.request
import urllib.error

from net import SSL_CONTEXT, describe_tls

API = "https://api.github.com"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
USER_AGENT = "SaveWaypoint/0.1"

# GitHub's Contents API rejects blobs over 100 MB and gets unreliable well before
# that once base64-encoded. Game saves are tiny, so a firm ceiling is a feature:
# it turns a silent failure into a clear message.
SOFT_LIMIT = 45 * 1024 * 1024
HARD_LIMIT = 90 * 1024 * 1024


class GitHubError(Exception):
    pass


def _net_error(exc):
    """Turn urllib's opaque URLError into something actionable."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
        return (f"TLS error: {reason}. Certificate store in use: {describe_tls()}")
    return f"network error: {reason}"


def _request(method, url, token=None, data=None, accept="application/json"):
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:
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
        raise GitHubError(_net_error(e))


def _request_raw(url, token):
    """GET a file's raw bytes straight from the Contents API (handles any size
    up to GitHub's limit, unlike the inline base64 field which caps at 1 MB)."""
    headers = {
        "Accept": "application/vnd.github.raw",
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise GitHubError(_net_error(e))


def _form_request(url, fields):
    """POST application/x-www-form-urlencoded, expect JSON back (device flow)."""
    body = urllib.parse.urlencode(fields).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            raise GitHubError(f"HTTP {e.code} from GitHub")
    except urllib.error.URLError as e:
        raise GitHubError(_net_error(e))


# --- Device flow -------------------------------------------------------------

# GitHub's actual error codes for this endpoint, mapped to something a user can
# act on without reading the API docs.
_DEVICE_START_ERRORS = {
    "device_flow_disabled":
        "Device Flow is not enabled on this OAuth App. Open "
        "github.com/settings/developers, pick the app, tick 'Enable Device Flow' "
        "and press Update application.",
    "unauthorized_client":
        "this OAuth App is not allowed to use Device Flow - tick 'Enable Device "
        "Flow' on its GitHub settings page.",
    "Not Found":
        "GitHub does not know this Client ID. Copy it again from "
        "github.com/settings/developers (it is not your username).",
    "incorrect_client_credentials":
        "GitHub rejected this Client ID - copy it again from the OAuth App page.",
}


def device_start(client_id, scope="repo"):
    r = _form_request(DEVICE_CODE_URL, {"client_id": client_id, "scope": scope})
    if "device_code" not in r:
        err = r.get("error") or ""
        raise GitHubError(
            _DEVICE_START_ERRORS.get(err)
            or r.get("error_description")
            or err
            or "GitHub refused the sign-in request")
    return r  # device_code, user_code, verification_uri, interval, expires_in


def device_poll(client_id, device_code):
    """Poll once. Returns (state, payload) where state is one of:
    'ok' | 'pending' | 'slow_down' | 'expired' | 'denied' | 'error'."""
    r = _form_request(TOKEN_URL, {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": DEVICE_GRANT,
    })
    if "access_token" in r:
        return "ok", r["access_token"]
    err = r.get("error")
    if err == "authorization_pending":
        return "pending", None
    if err == "slow_down":
        return "slow_down", None
    if err == "expired_token":
        return "expired", "the code expired - start again"
    if err == "access_denied":
        return "denied", "you declined the request on GitHub"
    return "error", r.get("error_description") or err or "unknown error"


# --- Account / repo ----------------------------------------------------------

def get_user(token):
    status, data = _request("GET", f"{API}/user", token=token)
    if status == 401:
        raise GitHubError("token rejected by GitHub (expired or revoked)")
    if status != 200:
        raise GitHubError(data.get("message", f"HTTP {status}"))
    return data


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
    # Let the auto_init commit land before the first content push.
    time.sleep(2)
    return data


# --- Archive helpers ---------------------------------------------------------

def make_archive_bytes(folder):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(folder, arcname=".")
    blob = buf.getvalue()
    if len(blob) > HARD_LIMIT:
        raise GitHubError(
            f"save is too large for the GitHub API ({len(blob) // (1024*1024)} MB, "
            f"limit {HARD_LIMIT // (1024*1024)} MB)")
    return blob


def extract_archive_bytes(raw, dest_folder):
    os.makedirs(dest_folder, exist_ok=True)
    buf = io.BytesIO(raw)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        _safe_extract(tar, dest_folder)


def _safe_extract(tar, dest):
    """Reject path traversal, then extract with the hardened filter when the
    running Python supports it (3.12+)."""
    dest_abs = os.path.abspath(dest)
    for member in tar.getmembers():
        target = os.path.abspath(os.path.join(dest, member.name))
        if target != dest_abs and not target.startswith(dest_abs + os.sep):
            raise GitHubError(f"unsafe path in archive: {member.name}")
        if member.issym() or member.islnk():
            link_target = os.path.abspath(
                os.path.join(os.path.dirname(target), member.linkname))
            if not link_target.startswith(dest_abs + os.sep):
                raise GitHubError(f"unsafe link in archive: {member.name}")
    try:
        tar.extractall(dest, filter="data")
    except TypeError:
        tar.extractall(dest)


def chown_tree(path, uid, gid):
    """After a restore performed as root, hand the files back to the game's user
    or the game will not be able to write its own save."""
    if uid is None or gid is None:
        return
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chown(os.path.join(root, name), uid, gid)
            except OSError:
                pass
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass


# --- Contents API (upload / download) ---------------------------------------

def _get_sha(token, owner, repo, path):
    status, data = _request("GET", f"{API}/repos/{owner}/{repo}/contents/{path}",
                            token=token)
    if status == 200 and isinstance(data, dict):
        return data.get("sha")
    return None


def put_file(token, owner, repo, path, content_bytes, message):
    if len(content_bytes) > HARD_LIMIT:
        raise GitHubError(f"{path}: too large for the GitHub API")
    sha = _get_sha(token, owner, repo, path)
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    status, data = _request("PUT", f"{API}/repos/{owner}/{repo}/contents/{path}",
                            token=token, data=payload)
    if status == 409:
        raise GitHubError("repository is busy (conflict) - try again")
    if status not in (200, 201):
        raise GitHubError(data.get("message", f"HTTP {status} on PUT {path}"))
    return data


def get_file(token, owner, repo, path, ref=None):
    url = f"{API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}"
    if ref:
        url += f"?ref={urllib.parse.quote(ref)}"
    status, raw = _request_raw(url, token)
    if status == 404:
        return None
    if status != 200:
        try:
            msg = json.loads(raw.decode("utf-8")).get("message", f"HTTP {status}")
        except Exception:
            msg = f"HTTP {status}"
        raise GitHubError(msg)
    return raw


def list_remote_entries(token, owner, repo):
    status, data = _request("GET", f"{API}/repos/{owner}/{repo}/contents/saves",
                            token=token)
    if status == 404:
        return []
    if status != 200:
        raise GitHubError(data.get("message", f"HTTP {status}"))
    return [d["name"] for d in data if d.get("type") == "dir"]


def file_history(token, owner, repo, path, limit=10):
    """Commits touching a path -> the version history shown in the UI."""
    url = (f"{API}/repos/{owner}/{repo}/commits"
           f"?path={urllib.parse.quote(path)}&per_page={limit}")
    status, data = _request("GET", url, token=token)
    if status != 200 or not isinstance(data, list):
        return []
    return [{
        "sha": c["sha"],
        "date": c["commit"]["committer"]["date"],
        "message": c["commit"]["message"],
    } for c in data]
