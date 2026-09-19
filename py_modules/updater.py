"""
SaveWaypoint - release channel / update check.

Same convention as the other plugins: the git tag carries the version from
package.json, and every release ships one asset with a fixed name so the plugin
can find it without guessing.

  stable : vX.Y.Z             -> GitHub "latest"
  beta   : vX.Y.Z-beta.N      -> pre-release, only offered on the beta channel
"""

import re
import json
import urllib.request
import urllib.error

REPO = "koua29/savewaypoint"
ZIP_NAME = "SaveWaypoint.zip"
UPDATE_INTERVAL = 86400  # at most one GitHub check per day
USER_AGENT = "SaveWaypoint/updater"

_PRERELEASE = re.compile(r"-(?:beta|rc|alpha)\.?(\d+)?", re.I)


def _http(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def version_key(version):
    """Sortable version: 0.2.0-beta.1 sits after 0.1.9 but before 0.2.0."""
    numbers = tuple(int(n) for n in re.findall(r"\d+", version.split("-", 1)[0])[:3])
    numbers += (0,) * (3 - len(numbers))
    pre = _PRERELEASE.search(version)
    return numbers + ((0, int(pre.group(1) or 0)) if pre else (1, 0))


def latest_release(current, beta=False):
    """Newest release on the chosen channel, compared with what is installed."""
    try:
        if beta:
            releases = [r for r in json.loads(
                _http(f"https://api.github.com/repos/{REPO}/releases?per_page=20"))
                if not r.get("draft")]
            release = max(releases,
                          key=lambda r: version_key(r.get("tag_name", "")),
                          default={})
        else:
            release = json.loads(
                _http(f"https://api.github.com/repos/{REPO}/releases/latest"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        # No releases yet, or the repo is private / unreachable.
        return None

    asset = next((a for a in release.get("assets", [])
                  if a.get("name") == ZIP_NAME), {})
    latest = release.get("tag_name", "").lstrip("v")
    digest = asset.get("digest") or ""
    return {
        "current": current,
        "latest": latest,
        "available": bool(asset) and version_key(latest) > version_key(current),
        # Leaving the beta channel while running a beta build: offer stable back.
        "rollback": bool(asset) and not beta and version_key(latest) < version_key(current),
        "prerelease": bool(release.get("prerelease")),
        "channel": "beta" if beta else "stable",
        "title": release.get("name", ""),
        "notes": (release.get("body") or "")[:800],
        "url": release.get("html_url", ""),
        "zip_url": asset.get("browser_download_url", ""),
        "zip_sha256": digest[7:] if digest.startswith("sha256:") else "",
    }
