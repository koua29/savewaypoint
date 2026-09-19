import {
  ButtonItem,
  ConfirmModal,
  DialogButton,
  Focusable,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import { Navigation } from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { FaMapPin, FaVial, FaDownload, FaHistory } from "react-icons/fa";

const PLUGIN_NAME = "SaveWaypoint";
const INSTALL_TYPE_UPDATE = 1;

// --- Backend bindings --------------------------------------------------------
const getStatus = callable<[], any>("get_status");
const setClientId = callable<[string], any>("set_client_id");
const clearClientId = callable<[], any>("clear_client_id");
const loginStart = callable<[], any>("login_start");
const loginPoll = callable<[], any>("login_poll");
const loginCancel = callable<[], any>("login_cancel");
const disconnect = callable<[], any>("disconnect");
const scan = callable<[boolean], any>("scan");
const setSelection = callable<[string[]], any>("set_selection");
const backup = callable<[string[] | null], any>("backup");
const restore = callable<[string, string | null], any>("restore");
const testEntry = callable<[string], any>("test_entry");
const setShowSteam = callable<[boolean], any>("set_show_steam");
const historyOf = callable<[string], any>("history");
const getVersion = callable<[], any>("get_version");
const setBeta = callable<[boolean], any>("set_beta");
const checkUpdate = callable<[boolean], any>("check_update");

type Update = {
  current: string;
  latest: string;
  available: boolean;
  rollback: boolean;
  prerelease: boolean;
  channel: string;
  title: string;
  url: string;
  zip_url: string;
  zip_sha256: string;
};

function openWeb(url: string) {
  Navigation.NavigateToExternalWeb(url);
  Navigation.CloseSideMenus();
}

async function installUpdate(update: Update) {
  // Same call Decky's own store uses: Decky prompts, verifies the zip's SHA-256
  // and swaps the plugin files in.
  const backend = (window as any).DeckyBackend;
  if (!backend?.call) {
    openWeb(update.url);
    return;
  }
  await backend.call(
    "utilities/install_plugin",
    update.zip_url,
    PLUGIN_NAME,
    update.latest,
    update.zip_sha256,
    INSTALL_TYPE_UPDATE
  );
  // Decky writes the files but this panel is still running the old bundle, so
  // ask the loader to import the new build before closing the menu.
  const loader = (window as any).DeckyPluginLoader;
  try {
    await (loader?.importPlugin?.(PLUGIN_NAME, update.latest) ??
      loader?.loadPlugin?.(PLUGIN_NAME) ??
      Promise.resolve());
  } catch (e) {
    console.error("[SaveWaypoint] plugin reload failed", e);
  }
  toaster.toast({ title: "SaveWaypoint", body: `Updated to ${update.latest}` });
  Navigation.CloseSideMenus();
}

type Entry = {
  id: string;
  name: string;
  source: string;
  status: "green" | "yellow" | "red";
  file_count: number;
  size_bytes: number;
  selected: boolean;
  steam_cloud: boolean;
};

type Commit = { sha: string; date: string; message: string };

/** Lets the user roll a save back to an earlier backup. Every backup is a git
 *  commit, so the commit list IS the version history. */
function VersionPicker({
  name,
  commits,
  onPick,
  closeModal,
}: {
  name: string;
  commits: Commit[];
  onPick: (sha: string) => void;
  closeModal?: () => void;
}) {
  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ fontWeight: "bold", marginBottom: "10px" }}>
        {`Earlier backups of ${name}`}
      </div>
      <div style={{ fontSize: "0.85em", opacity: 0.75, marginBottom: "12px" }}>
        Restoring overwrites the save on this device. This cannot be undone.
      </div>
      <Focusable style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {commits.map((c, i) => (
          <DialogButton
            key={c.sha}
            onClick={() => {
              closeModal?.();
              onPick(c.sha);
            }}
          >
            {`${new Date(c.date).toLocaleString()}${i === 0 ? "  (newest)" : ""}`}
          </DialogButton>
        ))}
      </Focusable>
    </ModalRoot>
  );
}

/** The full save list lives in a modal, not in the quick-access panel.
 *  The panel must stay short and a fixed height whatever the library size;
 *  inline, a dozen games turned it into an endless scroll. */
function SaveManagerModal({
  initial,
  onToggle,
  onTest,
  onRestore,
  onHistory,
  closeModal,
}: {
  initial: Entry[];
  onToggle: (id: string, on: boolean) => Promise<void>;
  onTest: (e: Entry) => void;
  onRestore: (e: Entry) => void;
  onHistory: (e: Entry) => void;
  closeModal?: () => void;
}) {
  const [items, setItems] = useState<Entry[]>(initial);
  const [onlyOn, setOnlyOn] = useState(false);

  const flip = async (id: string, on: boolean) => {
    setItems((list) => list.map((x) => (x.id === id ? { ...x, selected: on } : x)));
    await onToggle(id, on);
  };

  const shown = onlyOn ? items.filter((x) => x.selected) : items;
  const count = items.filter((x) => x.selected).length;

  return (
    <ModalRoot closeModal={closeModal}>
      <div style={{ fontWeight: "bold", marginBottom: "4px" }}>Manage saves</div>
      <div style={{ fontSize: "0.85em", opacity: 0.75, marginBottom: "10px" }}>
        {`${count} of ${items.length} will sync. Nothing is uploaded until you press Back up now.`}
      </div>
      <ToggleField
        label="Only show what I sync"
        checked={onlyOn}
        onChange={setOnlyOn}
      />
      <Focusable
        style={{
          display: "flex",
          flexDirection: "column",
          maxHeight: "55vh",
          overflowY: "auto",
        }}
      >
        {shown.map((e) => (
          <div key={e.id} style={{ borderBottom: "1px solid rgba(255,255,255,.08)" }}>
            <ToggleField
              label={`${DOT[e.status]} ${e.name}`}
              description={`${e.steam_cloud ? "☁️ " : ""}${HINT[e.status]} · ${
                e.file_count
              } files · ${humanSize(e.size_bytes)}`}
              checked={e.selected}
              disabled={e.status === "red"}
              onChange={(v) => flip(e.id, v)}
            />
            {e.selected && (
              <Focusable style={{ display: "flex", gap: "6px", padding: "0 0 10px" }}>
                <DialogButton
                  style={ACTION_BTN}
                  onClick={() => onTest(e)}
                  onOKActionDescription="Check it works (no upload)"
                >
                  <FaVial />
                </DialogButton>
                <DialogButton
                  style={ACTION_BTN}
                  onClick={() => onRestore(e)}
                  onOKActionDescription="Restore from cloud"
                >
                  <FaDownload />
                </DialogButton>
                <DialogButton
                  style={ACTION_BTN}
                  onClick={() => onHistory(e)}
                  onOKActionDescription="Older versions"
                >
                  <FaHistory />
                </DialogButton>
              </Focusable>
            )}
          </div>
        ))}
        {shown.length === 0 && (
          <div style={{ opacity: 0.7, padding: "12px 0" }}>Nothing to show.</div>
        )}
      </Focusable>
    </ModalRoot>
  );
}

const DOT: Record<string, string> = { green: "🟢", yellow: "🟡", red: "🔴" };

// Kept terse: the panel is ~310px wide, so a long line wraps to three rows and
// turns a ten-game list into a scrolling marathon.
const HINT: Record<string, string> = {
  green: "detected",
  yellow: "probable",
  red: "no save yet",
};

// Three buttons only fit across the panel without text labels. flex:1 with
// minWidth:0 stops DialogButton's default min-width from forcing an overflow.
const ACTION_BTN: CSSProperties = {
  flex: 1,
  minWidth: 0,
  padding: "6px 0",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

const humanSize = (b: number) => {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
};

const toast = (title: string, body: string) => toaster.toast({ title, body });

function Content() {
  const [status, setStatus] = useState<any>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [clientId, setCid] = useState("");
  const [device, setDevice] = useState<any>(null);
  const [polls, setPolls] = useState(0);
  const [busy, setBusy] = useState(false);
  const [scanned, setScanned] = useState(false);
  const [version, setVersion] = useState<any>(null);
  const [update, setUpdate] = useState<Update | null>(null);
  const scanning = useRef(false);

  const refresh = async () => {
    const s = await getStatus();
    setStatus(s);
    return s;
  };

  const doScan = async (force = true) => {
    if (scanning.current) return;
    scanning.current = true;
    setBusy(true);
    try {
      const r = await scan(force);
      if (r.ok) {
        setEntries(r.entries);
        setScanned(true);
      } else {
        toast("SaveWaypoint", `Scan failed: ${r.error}`);
      }
    } catch (e: any) {
      toast("Scan failed", String(e?.message ?? e));
    } finally {
      scanning.current = false;
      setBusy(false);
    }
  };

  // First load: fetch status and, when already connected, scan straight away so
  // the user lands on a populated list instead of an empty panel.
  useEffect(() => {
    (async () => {
      try {
        const s = await refresh();
        setVersion(await getVersion());
        if (s?.connected) doScan(false);
        const u = await checkUpdate(false);
        setUpdate(u && !u.error ? u : null);
      } catch (e) {
        // A failing update check must not stop the panel from rendering.
        console.error("[SaveWaypoint] startup", e);
      }
    })();
  }, []);

  const doCheckUpdate = async (force = true) => {
    setBusy(true);
    try {
      const u = await checkUpdate(force);
      setUpdate(u && !u.error ? u : null);
      if (!u) toast("SaveWaypoint", "Could not reach GitHub for updates");
      else if (u.error) toast("Update check failed", u.error);
      else if (!u.available && !u.rollback)
        // Naming the channel matters: "up to date" on stable while a newer beta
        // exists is otherwise indistinguishable from a broken check.
        toast("SaveWaypoint", `Up to date on the ${u.channel} channel`);
    } catch (e: any) {
      toast("Update check failed", String(e?.message ?? e));
    } finally {
      // Without this the button stays disabled forever on any failure.
      setBusy(false);
    }
  };

  const toggleBeta = async (on: boolean) => {
    setVersion((v: any) => ({ ...v, beta: on }));
    await setBeta(on);
    doCheckUpdate(true);
  };

  // Poll the device-flow login until the user approves it on their phone.
  // A self-scheduling timeout, not setInterval: each poll is async, so a fixed
  // interval would overlap requests, which is what makes GitHub answer
  // slow_down. The backend tells us how long to wait after a back-off.
  useEffect(() => {
    if (!device) return;
    let cancelled = false;
    let timer: any;
    let wait = (device.interval || 5) * 1000;

    const tick = async () => {
      if (cancelled) return;
      try {
        const r = await loginPoll();
        if (cancelled) return;
        if (r.state === "ok") {
          setDevice(null);
          toast("SaveWaypoint", `Connected as ${r.login}`);
          await refresh();
          doScan(true);
          return;
        }
        if (r.state === "error") {
          setDevice(null);
          toast("Sign-in failed", r.error);
          return;
        }
        if (r.interval) wait = r.interval * 1000;
        setPolls((n) => n + 1);
        if (r.warning) console.warn("[SaveWaypoint] poll", r.warning);
      } catch (e: any) {
        // Must not stop the loop: one failed request is not a failed sign-in.
        console.error("[SaveWaypoint] poll error", e);
        setPolls((n) => n + 1);
      }
      timer = setTimeout(tick, wait);
    };

    timer = setTimeout(tick, wait);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [device]);

  const doConnect = async () => {
    if (clientId.trim()) {
      const saved = await setClientId(clientId.trim());
      if (!saved.ok) {
        toast("Client ID", saved.error);
        return;
      }
      await refresh();
    }
    const r = await loginStart();
    if (!r.ok) {
      toast("SaveWaypoint", r.error);
      return;
    }
    setPolls(0);
    setDevice(r);
  };

  const toggle = async (id: string, on: boolean) => {
    const next = entries.map((e) => (e.id === id ? { ...e, selected: on } : e));
    setEntries(next);
    await setSelection(next.filter((e) => e.selected).map((e) => e.id));
  };

  const doBackup = async () => {
    setBusy(true);
    let r: any;
    try {
      r = await backup(null);
    } catch (e: any) {
      toast("Backup failed", String(e?.message ?? e));
      return;
    } finally {
      setBusy(false);
    }
    if (!r.ok) {
      toast("SaveWaypoint", `Error: ${r.error}`);
      return;
    }
    const failed = (r.results || []).filter((x: any) => !x.ok);
    toast(
      "SaveWaypoint",
      failed.length
        ? `Backed up ${r.count}/${r.total} — failed: ${failed
            .map((f: any) => f.name || f.id)
            .join(", ")}`
        : `Backed up ${r.count}/${r.total} game(s)`
    );
  };

  const runRestore = async (e: Entry, ref: string | null) => {
    setBusy(true);
    try {
      const r = await restore(e.id, ref);
      toast("SaveWaypoint", r.ok ? `Restored ${e.name}` : `Error: ${r.error}`);
      if (r.ok) doScan(true);
    } catch (err: any) {
      toast("Restore failed", String(err?.message ?? err));
    } finally {
      setBusy(false);
    }
  };

  // Restoring overwrites the local save, so always confirm first.
  const doRestore = (e: Entry) => {
    showModal(
      <ConfirmModal
        strTitle={`Restore ${e.name}?`}
        strDescription={
          "This overwrites the save files currently on this device with the " +
          "version stored in your GitHub repo. This cannot be undone."
        }
        strOKButtonText="Restore"
        onOK={() => runRestore(e, null)}
      />
    );
  };

  const doHistory = async (e: Entry) => {
    setBusy(true);
    let r: any;
    try {
      r = await historyOf(e.id);
    } catch (err: any) {
      toast("History failed", String(err?.message ?? err));
      return;
    } finally {
      setBusy(false);
    }
    const commits: Commit[] = r?.commits ?? [];
    if (!r?.ok || commits.length === 0) {
      toast(e.name, r?.error ? `⚠️ ${r.error}` : "No backup history yet");
      return;
    }
    showModal(
      <VersionPicker
        name={e.name}
        commits={commits}
        onPick={(sha) => runRestore(e, sha)}
      />
    );
  };

  // The manager keeps its own copy while open; `toggle` also updates the panel's
  // list, so the "x of y will sync" summary stays right after it closes.
  const openManager = () =>
    showModal(
      <SaveManagerModal
        initial={entries}
        onToggle={toggle}
        onTest={doTest}
        onRestore={doRestore}
        onHistory={doHistory}
      />
    );

  const doTest = async (e: Entry) => {
    const r = await testEntry(e.id);
    if (!r.ok) {
      toast(e.name, `⚠️ ${r.error}`);
      return;
    }
    // Says what was actually proven, so "Test" has an obvious purpose: this save
    // can be backed up AND put back, checked without uploading anything.
    const parts = [
      r.restorable
        ? "✅ can be backed up and restored"
        : "⚠️ readable, but the folder is read-only — a restore would fail",
      humanSize(r.archive_size),
    ];
    if (r.warn_large) parts.push("⚠️ large");
    toast(e.name, parts.join(" · "));
  };

  if (!status) return <PanelSection title="SaveWaypoint">Loading…</PanelSection>;

  const runningBeta = /-(?:beta|rc|alpha)/i.test(version?.version ?? "");

  // Shown on both the connected and the not-yet-connected screens, so an update
  // is never gated behind signing in.
  const updateSection = (
    <PanelSection title="Version">
      <PanelSectionRow>
        <span style={{ fontSize: "0.9em", opacity: 0.8 }}>
          {`v${version?.version ?? "?"}`}
          {version?.beta ? " · beta channel" : ""}
        </span>
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Beta channel"
          description="Pre-releases: newer, less tested."
          checked={!!version?.beta}
          onChange={toggleBeta}
        />
      </PanelSectionRow>
      {/* Running a pre-release while the channel is off is a dead end: newer
          betas are filtered out, so nothing ever shows up. Say so. */}
      {runningBeta && !version?.beta && (
        <PanelSectionRow>
          <span style={{ fontSize: "0.85em", opacity: 0.9 }}>
            You are running a beta build while the beta channel is off, so newer
            betas are hidden. Turn on <b>Beta channel</b> to receive them.
          </span>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => doCheckUpdate(true)} disabled={busy}>
          Check for updates
        </ButtonItem>
      </PanelSectionRow>
      {update && (update.available || update.rollback) && (
        <>
          <PanelSectionRow>
            <div style={{ fontSize: "0.9em" }}>
              {update.available
                ? `Update available: ${update.latest}`
                : `Go back to stable: ${update.latest}`}
              {update.prerelease && " [beta]"}
              {update.title && <div style={{ fontWeight: "bold" }}>{update.title}</div>}
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => installUpdate(update)}>
              {update.available ? `Update to ${update.latest}` : `Install ${update.latest}`}
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => openWeb(update.url)}>
              View release notes
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );

  // --- Not connected: setup + device flow -----------------------------------
  if (!status.connected) {
    if (device) {
      return (
        <PanelSection title="Connect GitHub">
          <PanelSectionRow>
            On your phone, open <b>{device.verification_uri}</b> and enter this code:
          </PanelSectionRow>
          <PanelSectionRow>
            <div
              style={{
                fontSize: "2em",
                fontWeight: "bold",
                letterSpacing: "4px",
                textAlign: "center",
                padding: "8px 0",
              }}
            >
              {device.user_code}
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            {`Waiting for approval… (checked ${polls}×)`}
          </PanelSectionRow>
          {polls >= 6 && (
            <PanelSectionRow>
              <span style={{ fontSize: "0.85em", opacity: 0.85 }}>
                Already approved on GitHub? Cancel and press Connect again — the
                code may have expired.
              </span>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={async () => {
                await loginCancel();
                setDevice(null);
              }}
            >
              Cancel
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      );
    }
    return (
      <>
        <PanelSection title="Connect GitHub">
          {status.has_client_id ? (
            <PanelSectionRow>
              <span style={{ fontSize: "0.85em", opacity: 0.8 }}>
                A Client ID is saved. Wrong one? Clear it and paste another.
              </span>
            </PanelSectionRow>
          ) : (
            <PanelSectionRow>
              <TextField
                label="GitHub OAuth Client ID"
                description="From your OAuth App on github.com — starts with 'Ov23li'. This is NOT your username."
                value={clientId}
                onChange={(e) => setCid(e.target.value)}
              />
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={doConnect}>
              Connect GitHub
            </ButtonItem>
          </PanelSectionRow>
          {status.has_client_id && (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={async () => {
                  await clearClientId();
                  setCid("");
                  refresh();
                }}
              >
                Clear Client ID
              </ButtonItem>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <span style={{ fontSize: "0.8em", opacity: 0.7 }}>
              Your saves go to a private repo on your own account. See the README to
              create the one-time OAuth App Client ID.
            </span>
          </PanelSectionRow>
        </PanelSection>
        {updateSection}
      </>
    );
  }

  // --- Connected: main UI ----------------------------------------------------
  const selected = entries.filter((e) => e.selected);
  return (
    <>
      <PanelSection title={`Connected · ${status.login}`}>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => doScan(true)} disabled={busy}>
            {busy ? "Working…" : "Rescan for saves"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={doBackup}
            disabled={busy || selected.length === 0}
          >
            {`Back up now (${selected.length})`}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Saves">
        <PanelSectionRow>
          <span style={{ fontSize: "0.85em", opacity: 0.8 }}>
            {entries.length === 0
              ? scanned
                ? "Nothing found yet. Launch a game, save once, then rescan."
                : "Scanning…"
              : `${selected.length} of ${entries.length} will sync`}
          </span>
        </PanelSectionRow>
        {entries.length > 0 && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={openManager}>
              Manage saves…
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Options">
        <PanelSectionRow>
          <ToggleField
            label="Include Steam games"
            description="Steam Cloud already syncs these."
            checked={!!status.show_steam}
            onChange={async (v) => {
              setStatus((s: any) => ({ ...s, show_steam: v }));
              await setShowSteam(v);
              doScan(true);
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              await disconnect();
              setEntries([]);
              setScanned(false);
              refresh();
            }}
          >
            Disconnect
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      {updateSection}
    </>
  );
}

export default definePlugin(() => ({
  name: "SaveWaypoint",
  titleView: <div className={staticClasses.Title}>SaveWaypoint</div>,
  content: <Content />,
  icon: <FaMapPin />,
}));
