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

HOME = os.path.expanduser("~")


def _p(*parts):
    return os.path.join(HOME, *parts)


# --- Known emulator save locations -------------------------------------------
# Each entry: display name + a list of candidate paths (native install and the
# Flatpak layout used by EmuDeck). We keep the FIRST existing candidate.
EMULATORS = [
    ("RetroArch", [
        ".config/retroarch/saves",
        ".config/retroarch/states",
        ".var/app/org.libretro.RetroArch/config/retroarch/saves",
        ".var/app/org.libretro.RetroArch/config/retroarch/states",
        "Emulation/saves/retroarch",
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
    ("EmuDeck saves", [
        "Emulation/saves",
        "Emulation/storage",
    ]),
]

# Sub-folders inside a Proton prefix that typically hold saves.
PROTON_SAVE_HINTS = [
    "drive_c/users/steamuser/Documents/My Games",
    "drive_c/users/steamuser/Documents/SavedGames",
    "drive_c/users/steamuser/Saved Games",
    "drive_c/users/steamuser/AppData/Roaming",
    "drive_c/users/steamuser/AppData/LocalLow",
    "drive_c/users/steamuser/AppData/Local",
    "drive_c/users/steamuser/Documents",
]

STEAM_COMPATDATA = [
    ".local/share/Steam/steamapps/compatdata",
    ".steam/steam/steamapps/compatdata",
]

# Folders we never treat as game saves (noise inside AppData/Documents).
PROTON_BLOCKLIST = {
    "Microsoft", "Temp", "Packages", "ConnectedDevicesPlatform",
    "CrashDumps", "D3DSCache", "NVIDIA", "Steam", "vfleet", "Google",
}


def _stat_folder(path, max_files=20000):
    """Return (file_count, total_bytes, newest_mtime) for a folder tree."""
    count = 0
    total = 0
    newest = 0.0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            count += 1
            total += st.st_size
            if st.st_mtime > newest:
                newest = st.st_mtime
            if count >= max_files:
                return count, total, newest
    return count, total, newest


def _make_id(source, path):
    h = hashlib.sha1(f"{source}:{path}".encode("utf-8")).hexdigest()[:12]
    return h


def _entry(name, source, path, status):
    count, size, newest = (0, 0, 0.0)
    if os.path.isdir(path):
        count, size, newest = _stat_folder(path)
    # A known path that exists but is empty -> red (game never saved yet).
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
    }


def scan_emulators():
    out = []
    seen = set()
    for name, candidates in EMULATORS:
        for rel in candidates:
            path = _p(rel)
            if not os.path.isdir(path):
                continue
            if path in seen:
                continue
            seen.add(path)
            label = name if rel.split("/")[-1] not in ("saves", "states", "memcards") \
                else f"{name} · {rel.split('/')[-1]}"
            out.append(_entry(label, "emulator", path, "green"))
    return out


def scan_proton(min_age_days=None, recent_days=365):
    """Heuristic: look inside each Proton prefix for save-like folders that were
    modified recently. Returns YELLOW entries (need user confirmation)."""
    out = []
    seen = set()
    cutoff = time.time() - recent_days * 86400
    for base_rel in STEAM_COMPATDATA:
        base = _p(base_rel)
        if not os.path.isdir(base):
            continue
        try:
            appids = os.listdir(base)
        except OSError:
            continue
        for appid in appids:
            pfx = os.path.join(base, appid, "pfx")
            if not os.path.isdir(pfx):
                continue
            for hint in PROTON_SAVE_HINTS:
                hint_path = os.path.join(pfx, hint)
                if not os.path.isdir(hint_path):
                    continue
                try:
                    children = os.listdir(hint_path)
                except OSError:
                    continue
                for child in children:
                    if child in PROTON_BLOCKLIST:
                        continue
                    cpath = os.path.join(hint_path, child)
                    if not os.path.isdir(cpath) or cpath in seen:
                        continue
                    count, size, newest = _stat_folder(cpath, max_files=2000)
                    if count == 0 or newest < cutoff:
                        continue
                    seen.add(cpath)
                    name = f"{child} (Proton #{appid})"
                    e = _entry(name, "proton", cpath, "yellow")
                    out.append(e)
    return out


def scan_watched(watched_paths):
    """User-added folders. Always GREEN (they confirmed it)."""
    out = []
    for path in watched_paths:
        path = os.path.expanduser(path)
        name = os.path.basename(path.rstrip("/")) or path
        out.append(_entry(f"{name} (custom)", "watched", path, "green"))
    return out


def full_scan(watched_paths=None):
    watched_paths = watched_paths or []
    entries = []
    entries.extend(scan_emulators())
    entries.extend(scan_proton())
    entries.extend(scan_watched(watched_paths))
    # Sort: green first, then yellow, then red; by newest activity inside group.
    order = {"green": 0, "yellow": 1, "red": 2}
    entries.sort(key=lambda e: (order.get(e["status"], 9), -e["last_modified"]))
    return entries
