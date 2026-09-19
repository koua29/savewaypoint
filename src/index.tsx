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
import { useEffect, useRef, useState } from "react";
import { FaMapPin } from "react-icons/fa";

const PLUGIN_NAME = "SaveWaypoint";
const INSTALL_TYPE_UPDATE = 1;

// --- Backend bindings --------------------------------------------------------
const getStatus = callable<[], any>("get_status");
const setClientId = callable<[string], any>("set_client_id");
const loginStart = callable<[], any>("login_start");
const loginPoll = callable<[], any>("login_poll");
const loginCancel = callable<[], any>("login_cancel");
const disconnect = callable<[], any>("disconnect");
const scan = callable<[boolean], any>("scan");
const setSelection = callable<[string[]], any>("set_selection");
const backup = callable<[string[] | null], any>("backup");
const restore = callable<[string, string | null], any>("restore");
const testEntry = callable<[string], any>("test_entry");
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

const DOT: Record<string, string> = { green: "🟢", yellow: "🟡", red: "🔴" };

const HINT: Record<string, string> = {
  green: "detected",
  yellow: "probable — confirm before syncing",
  red: "nothing saved yet",
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
    } finally {
      scanning.current = false;
      setBusy(false);
    }
  };

  // First load: fetch status and, when already connected, scan straight away so
  // the user lands on a populated list instead of an empty panel.
  useEffect(() => {
    (async () => {
      const s = await refresh();
      setVersion(await getVersion());
      if (s?.connected) doScan(false);
      setUpdate(await checkUpdate(false));
    })();
  }, []);

  const doCheckUpdate = async (force = true) => {
    setBusy(true);
    const u = await checkUpdate(force);
    setBusy(false);
    setUpdate(u);
    if (!u) toast("SaveWaypoint", "Could not reach GitHub for updates");
    else if (!u.available && !u.rollback) toast("SaveWaypoint", "You're up to date");
  };

  const toggleBeta = async (on: boolean) => {
    setVersion((v: any) => ({ ...v, beta: on }));
    await setBeta(on);
    doCheckUpdate(true);
  };

  // Poll the device-flow login until the user approves it on their phone.
  useEffect(() => {
    if (!device) return;
    const iv = setInterval(async () => {
      const r = await loginPoll();
      if (r.state === "ok") {
        clearInterval(iv);
        setDevice(null);
        toast("SaveWaypoint", `Connected as ${r.login}`);
        await refresh();
        doScan(true);
      } else if (r.state === "error") {
        clearInterval(iv);
        setDevice(null);
        toast("SaveWaypoint", `Login failed: ${r.error}`);
      }
    }, (device.interval || 5) * 1000);
    return () => clearInterval(iv);
  }, [device]);

  const doConnect = async () => {
    if (clientId.trim()) await setClientId(clientId.trim());
    const r = await loginStart();
    if (!r.ok) {
      toast("SaveWaypoint", r.error);
      return;
    }
    setDevice(r);
  };

  const toggle = async (id: string, on: boolean) => {
    const next = entries.map((e) => (e.id === id ? { ...e, selected: on } : e));
    setEntries(next);
    await setSelection(next.filter((e) => e.selected).map((e) => e.id));
  };

  const doBackup = async () => {
    setBusy(true);
    const r = await backup(null);
    setBusy(false);
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
    const r = await restore(e.id, ref);
    setBusy(false);
    toast("SaveWaypoint", r.ok ? `Restored ${e.name}` : `Error: ${r.error}`);
    if (r.ok) doScan(true);
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
    const r = await historyOf(e.id);
    setBusy(false);
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

  const doTest = async (e: Entry) => {
    const r = await testEntry(e.id);
    if (!r.ok) {
      toast(e.name, `⚠️ ${r.error}`);
      return;
    }
    const parts = [
      "✅ readable",
      r.restorable ? "restorable" : "⚠️ folder is read-only",
      humanSize(r.archive_size),
    ];
    if (r.warn_large) parts.push("⚠️ large");
    toast(e.name, parts.join(" · "));
  };

  if (!status) return <PanelSection title="SaveWaypoint">Loading…</PanelSection>;

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
          description="Receive pre-releases. Newer features, less tested."
          checked={!!version?.beta}
          onChange={toggleBeta}
        />
      </PanelSectionRow>
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
          <PanelSectionRow>Waiting for approval…</PanelSectionRow>
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
          {!status.has_client_id && (
            <PanelSectionRow>
              <TextField
                label="GitHub OAuth Client ID"
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

      <PanelSection title="Detected saves">
        {entries.length === 0 && (
          <PanelSectionRow>
            {scanned
              ? "Nothing found yet. Launch a game, save once, then rescan."
              : "Scanning…"}
          </PanelSectionRow>
        )}
        {entries.map((e) => (
          <PanelSectionRow key={e.id}>
            <Focusable style={{ display: "flex", flexDirection: "column", width: "100%" }}>
              <ToggleField
                label={`${DOT[e.status]} ${e.name}`}
                description={`${HINT[e.status]} · ${e.file_count} files · ${humanSize(
                  e.size_bytes
                )}`}
                checked={e.selected}
                disabled={e.status === "red"}
                onChange={(v) => toggle(e.id, v)}
              />
              <Focusable style={{ display: "flex", gap: "8px", paddingBottom: "8px" }}>
                <DialogButton style={{ flex: 1 }} onClick={() => doTest(e)}>
                  Test
                </DialogButton>
                <DialogButton
                  style={{ flex: 1 }}
                  disabled={busy}
                  onClick={() => doRestore(e)}
                >
                  Restore
                </DialogButton>
                <DialogButton
                  style={{ flex: 1 }}
                  disabled={busy}
                  onClick={() => doHistory(e)}
                >
                  History
                </DialogButton>
              </Focusable>
            </Focusable>
          </PanelSectionRow>
        ))}
      </PanelSection>

      <PanelSection title="Account">
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
