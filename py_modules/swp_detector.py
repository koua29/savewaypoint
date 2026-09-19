"""
SaveWaypoint - save detection engine.

Finds game-save locations on a SteamOS / Linux handheld WITHOUT relying on any
third-party licensed database (no PCGamingWiki manifest). Two clean sources:

  1. Known emulator paths  -> a save location is a public technical fact,
     documented by each emulator itself. Result: status GREEN (certain).
  2. Proton prefix scan    -> heuristic over the Wine prefix of Steam / non-Steam
     shortcuts. We cannot be 100% sure, so: status YELLOW (probable).

Anything the user adds by hand is a "watched folder" (GREEN, user-confirmed).

Status codes returned to the UI:
  "green"  -> detected, files present, safe to sync
  "yellow" -> probable (heuristic), ask the user to confirm before enabling
  "red"    -> expected but nothing found (e.g. game never saved yet)
"""

import os
import time
import hashlib

# Decky may run the plugin backend as a different user than the one who owns the
# games. DECKY_USER_HOME is the home of the actual Steam user and is the only
# reliable base -- expanduser("~") would resolve to /root and find nothing.
HOME = os.environ.get("DECKY_USER_HOME") or os.path.expanduser("~")

# Safety rails so a pathological folder can never hang the UI.
MAX_FILES_PER_ENTRY = 20000
MAX_PROTON_CANDIDATES = 120
PROTON_RECENT_DAYS = 365


def _p(*parts):
    return os.path.join(HOME, *parts)


# --- Known emulator save locations -------------------------------------------
# Each entry: display name + candidate paths (native install and the Flatpak
# layout used by EmuDeck). Every existing candidate becomes its own entry.
EMULATORS = [
    ("RetroArch", [
        ".config/retroarch/saves",
        ".config/retroarch/states",
        ".var/app/org.libretro.RetroArch/config/retroarch/saves",
        ".var/app/org.libretro.RetroArch/config/retroarch/states",
    ]),
    ("Dolphin (GC/Wii)", [
        ".local/share/dolphin-emu/GC",
        ".local/share/dolphin-emu/Wii",
        ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/GC",
        ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/Wii",
    ]),
    ("PCSX2 (PS2)", [
        ".config/PCSX2/memcards",
        ".config/PCSX2/sstates",
        ".var/app/net.pcsx2.PCSX2/config/PCSX2/memcards",
        ".var/app/net.pcsx2.PCSX2/config/PCSX2/sstates",
    ]),
    ("RPCS3 (PS3)", [
        ".config/rpcs3/dev_hdd0/home/00000001/savedata",
        ".var/app/net.rpcs3.RPCS3/config/rpcs3/dev_hdd0/home/00000001/savedata",
    ]),
    ("Ryujinx (Switch)", [
        ".config/Ryujinx/bis/user/save",
        ".var/app/org.ryujinx.Ryujinx/config/Ryujinx/bis/user/save",
    ]),
    ("PPSSPP (PSP)", [
        ".config/ppsspp/PSP/SAVEDATA",
        ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/SAVEDATA",
    ]),
    ("DuckStation (PS1)", [
        ".local/share/duckstation/memcards",
        ".local/share/duckstation/savestates",
        ".var/app/org.duckstation.DuckStation/data/duckstation/memcards",
    ]),
    ("melonDS (DS)", [
        ".config/melonDS",
        ".var/app/net.kuribo64.melonDS/config/melonDS",
    ]),
    ("mGBA (GBA)", [
        ".config/mgba",
        ".var/app/io.mgba.mGBA/config/mgba",
    ]),
    ("Flycast (Dreamcast)", [
        ".local/share/flycast",
        ".var/app/org.flycast.Flycast/data/flycast",
    ]),
    ("Cemu (Wii U)", [
        ".local/share/Cemu/mlc01/usr/save",
        ".var/app/info.cemu.Cemu/data/Cemu/mlc01/usr/save",
    ]),
    ("Xemu (Xbox)", [
        ".local/share/xemu/xemu",
        ".var/app/app.xemu.xemu/data/xemu/xemu",
    ]),
    ("Vita3K (PS Vita)", [
        ".local/share/Vita3K/Vita3K/ux0/user",
    ]),
    ("EmuDeck (central saves)", [
        "Emulation/saves",
    ]),
]

# Proton prefix hints.
# STRONG: every child folder is a plausible save -> yellow.
# WEAK:   only children that look save-related (name contains "save") -> yellow,
#         otherwise we'd flood the list with config/telemetry folders.
STRONG_HINTS = [
    "drive_c/users/steamuser/Documents/My Games",
    "drive_c/users/steamuser/Saved Games",
    "drive_c/users/steamuser/Documents/SavedGames",
]
WEAK_HINTS = [
    "drive_c/users/steamuser/AppData/Roaming",
    "drive_c/users/steamuser/AppData/LocalLow",
    "drive_c/users/steamuser/AppData/Local",
    "drive_c/users/steamuser/Documents",
]

STEAM_COMPATDATA = [
    ".local/share/Steam/steamapps/compatdata",
    ".steam/steam/steamapps/compatdata",
]

# Folders that are never game saves (noise inside AppData / Documents).
PROTON_BLOCKLIST = {
    "Microsoft", "Temp", "Packages", "ConnectedDevicesPlatform", "CrashDumps",
    "D3DSCache", "NVIDIA", "NVIDIA Corporation", "Steam", "Google", "Mozilla",
    "Adobe", "Intel", "AMD", "Valve", "wineboot", "Programs", "Comms",
    "IsolatedStorage", "History", "Application Data", "GameBarPresenceWriter",
    # Already walked as STRONG hints; reaching them again through the weaker
    # "Documents" hint would list the whole container next to its own games.
    "My Games", "Saved Games", "SavedGames",
}

STEAM_USERDATA = [
    ".local/share/Steam/userdata",
    ".steam/steam/userdata",
]

# Steam gives a non-Steam shortcut a generated AppID with the top bit set, so it
# always lands above 2^31; real Steam AppIDs are ordinary small integers. That is
# what separates "Steam Cloud already covers this" from "nobody is backing it up".
NON_STEAM_APPID_MIN = 2 ** 31


def _parse_binary_vdf(data):
    """Minimal reader for Steam's binary VDF (shortcuts.vdf):
    0x00 map, 0x01 string, 0x02 int32, 0x07 uint64, 0x08 end-of-map."""
    pos = 0

    def cstring():
        nonlocal pos
        end = data.index(b"\x00", pos)
        s = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return s

    def parse_map():
        nonlocal pos
        out = {}
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind == 0x08:
                return out
            key = cstring().lower()
            if kind == 0x00:
                out[key] = parse_map()
            elif kind == 0x01:
                out[key] = cstring()
            elif kind == 0x02:
                out[key] = int.from_bytes(data[pos:pos + 4], "little")
                pos += 4
            elif kind == 0x07:
                out[key] = int.from_bytes(data[pos:pos + 8], "little")
                pos += 8
            else:
                raise ValueError(f"unknown VDF type {kind}")
        return out

    return parse_map()


def shortcut_names():
    """AppID -> name for non-Steam games added to Steam, from each user's
    shortcuts.vdf. Best effort: a parse failure only costs nicer labels, so it
    must never break a scan."""
    names = {}
    for base_rel in STEAM_USERDATA:
        base = _p(base_rel)
        if not os.path.isdir(base):
            continue
        try:
            users = os.listdir(base)
        except OSError:
            continue
        for user in users:
            path = os.path.join(base, user, "config", "shortcuts.vdf")
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as f:
                    parsed = _parse_binary_vdf(f.read())
            except Exception:
                continue
            for entry in (parsed.get("shortcuts") or {}).values():
                if not isinstance(entry, dict):
                    continue
                appid, name = entry.get("appid"), entry.get("appname")
                if isinstance(appid, int) and name:
                    names[appid] = name
    return names


def _stat_folder(path, max_files=MAX_FILES_PER_ENTRY):
    """Return (file_count, total_bytes, newest_mtime, truncated)."""
    count = 0
    total = 0
    newest = 0.0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            count += 1
            total += st.st_size
            if st.st_mtime > newest:
                newest = st.st_mtime
            if count >= max_files:
                return count, total, newest, True
    return count, total, newest, False


def _make_id(source, path):
    return hashlib.sha1(f"{source}:{path}".encode("utf-8")).hexdigest()[:12]


def _entry(name, source, path, status, stats=None):
    # Canonical path: ~/.steam/steam is a symlink to ~/.local/share/Steam on
    # SteamOS, so the same folder reached by both spellings would otherwise be
    # listed (and archived) twice.
    path = os.path.realpath(path)
    if stats is None:
        stats = _stat_folder(path) if os.path.isdir(path) else (0, 0, 0.0, False)
    count, size, newest, truncated = stats
    # A known location that exists but is empty -> the game simply never saved.
    if status == "green" and count == 0:
        status = "red"
    return {
        "id": _make_id(source, path),
        "name": name,
        "source": source,
        "path": path,
        "status": status,
        "file_count": count,
        "size_bytes": size,
        "last_modified": newest,
        "truncated": truncated,
        # True only for real Steam games, which Steam Cloud already syncs.
        "steam_cloud": False,
        "appid": 0,
    }


def _looks_like_save(path, name):
    """Cheap heuristic for WEAK hints: the folder or one of its immediate
    children mentions 'save'."""
    if "save" in name.lower():
        return True
    try:
        for child in os.listdir(path)[:60]:
            if "save" in child.lower():
                return True
    except OSError:
        pass
    return False


def scan_emulators():
    out = []
    for name, candidates in EMULATORS:
        for rel in candidates:
            path = _p(rel)
            if not os.path.isdir(path):
                continue
            leaf = os.path.basename(rel)
            # Only append the leaf when it adds information (saves vs states).
            label = name if leaf.lower() in name.lower() else f"{name} · {leaf}"
            out.append(_entry(label, "emulator", path, "green"))
    return out


def scan_proton():
    """Heuristic pass over Proton prefixes -> YELLOW entries (need confirming).

    compatdata holds both real Steam games and non-Steam shortcuts. Each entry is
    tagged so the UI can hide the Steam ones, which Steam Cloud already covers and
    which we should not be racing to back up in parallel.
    """
    out = []
    seen = set()
    cutoff = time.time() - PROTON_RECENT_DAYS * 86400
    names = shortcut_names()

    for base_rel in STEAM_COMPATDATA:
        base = _p(base_rel)
        if not os.path.isdir(base):
            continue
        try:
            appids = sorted(os.listdir(base))
        except OSError:
            continue
        for appid in appids:
            if len(out) >= MAX_PROTON_CANDIDATES:
                return out
            pfx = os.path.join(base, appid, "pfx")
            if not os.path.isdir(pfx):
                continue
            for hint, strong in [(h, True) for h in STRONG_HINTS] + \
                                [(h, False) for h in WEAK_HINTS]:
                hint_path = os.path.join(pfx, hint)
                if not os.path.isdir(hint_path):
                    continue
                try:
                    children = os.listdir(hint_path)
                except OSError:
                    continue
                for child in children:
                    if child in PROTON_BLOCKLIST or child.startswith("."):
                        continue
                    cpath = os.path.join(hint_path, child)
                    if not os.path.isdir(cpath):
                        continue
                    real = os.path.realpath(cpath)
                    if real in seen:
                        continue
                    if not strong and not _looks_like_save(cpath, child):
                        continue
                    # Cheap pre-filter on the folder's own mtime before walking.
                    try:
                        if os.stat(cpath).st_mtime < cutoff:
                            continue
                    except OSError:
                        continue
                    stats = _stat_folder(cpath, max_files=2000)
                    if stats[0] == 0 or stats[2] < cutoff:
                        continue
                    seen.add(real)
                    try:
                        appid_n = int(appid)
                    except ValueError:
                        appid_n = 0
                    is_steam = 0 < appid_n < NON_STEAM_APPID_MIN
                    game = names.get(appid_n)
                    if game:
                        label = f"{child} — {game}"
                    elif is_steam:
                        label = f"{child} (Steam app {appid})"
                    else:
                        label = f"{child} (non-Steam {appid})"
                    e = _entry(label, "steam" if is_steam else "proton",
                               cpath, "yellow", stats)
                    e["steam_cloud"] = is_steam
                    e["appid"] = appid_n
                    out.append(e)
                    if len(out) >= MAX_PROTON_CANDIDATES:
                        return out
    return out


def scan_watched(watched_paths):
    """User-added folders. Always GREEN (the user confirmed them)."""
    out = []
    for path in watched_paths:
        path = os.path.expanduser(path)
        name = os.path.basename(path.rstrip("/")) or path
        out.append(_entry(f"{name} (custom)", "watched", path, "green"))
    return out


def _drop_nested(entries):
    """Remove entries whose path lives inside another entry's path, so the same
    files are never archived twice (e.g. EmuDeck's central saves folder vs the
    per-emulator folders it contains)."""
    kept = []
    paths = sorted(entries, key=lambda e: len(e["path"]))
    for e in paths:
        p = os.path.normpath(e["path"])
        if any(p.startswith(os.path.normpath(k["path"]) + os.sep) for k in kept):
            continue
        kept.append(e)
    # Preserve caller-facing ordering separately.
    kept_ids = {e["id"] for e in kept}
    return [e for e in entries if e["id"] in kept_ids]


def full_scan(watched_paths=None, show_steam=False):
    """show_steam=False (the default) drops real Steam games: Steam Cloud already
    syncs them, so listing them is noise at best and a second writer racing Steam
    at worst. Games without Cloud support are the reason it stays switchable."""
    entries = []
    entries.extend(scan_emulators())
    entries.extend(scan_proton())
    entries.extend(scan_watched(watched_paths or []))
    if not show_steam:
        entries = [e for e in entries if not e.get("steam_cloud")]

    # De-duplicate identical paths, then drop nested ones.
    unique = {}
    for e in entries:
        unique.setdefault(e["id"], e)
    entries = _drop_nested(list(unique.values()))

    order = {"green": 0, "yellow": 1, "red": 2}
    entries.sort(key=lambda e: (order.get(e["status"], 9), -e["last_modified"]))
    return entries
