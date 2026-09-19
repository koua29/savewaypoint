import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  TextField,
  Focusable,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaMapPin } from "react-icons/fa";

// --- Backend bindings --------------------------------------------------------
const getStatus = callable<[], any>("get_status");
const setClientId = callable<[string], any>("set_client_id");
const loginStart = callable<[], any>("login_start");
const loginPoll = callable<[], any>("login_poll");
const disconnect = callable<[], any>("disconnect");
const scan = callable<[], any>("scan");
const setSelection = callable<[string[]], any>("set_selection");
const backup = callable<[string[] | null], any>("backup");
const restore = callable<[string, string | null], any>("restore");
const testEntry = callable<[string], any>("test_entry");

type Entry = {
  id: string;
  name: string;
  source: string;
  status: "green" | "yellow" | "red";
  file_count: number;
  size_bytes: number;
  selected: boolean;
};

const statusDot = (s: string) =>
  s === "green" ? "🟢" : s === "yellow" ? "🟡" : "🔴";

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

function Content() {
  const [status, setStatus] = useState<any>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [clientId, setCid] = useState("");
  const [device, setDevice] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => setStatus(await getStatus());

  useEffect(() => {
    refresh();
  }, []);

  // Poll the device-flow login until approved.
  useEffect(() => {
    if (!device) return;
    const iv = setInterval(async () => {
      const r = await loginPoll();
      if (r.state === "ok") {
        clearInterval(iv);
        setDevice(null);
        toaster.toast({ title: "SaveWaypoint", body: `Connected as ${r.login}` });
        refresh();
      } else if (r.state === "error") {
        clearInterval(iv);
        setDevice(null);
        toaster.toast({ title: "SaveWaypoint", body: `Login error: ${r.error}` });
      }
    }, (device.interval || 5) * 1000);
    return () => clearInterval(iv);
  }, [device]);

  const doConnect = async () => {
    if (clientId.trim()) await setClientId(clientId.trim());
    const r = await loginStart();
    if (!r.ok) {
      toaster.toast({ title: "SaveWaypoint", body: r.error });
      return;
    }
    setDevice(r);
  };

  const doScan = async () => {
    setBusy(true);
    const r = await scan();
    setBusy(false);
    if (r.ok) setEntries(r.entries);
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
    toaster.toast({
      title: "SaveWaypoint",
      body: r.ok ? `Backed up ${r.count}/${r.total} game(s)` : `Error: ${r.error}`,
    });
  };

  const doRestore = async (e: Entry) => {
    setBusy(true);
    const r = await restore(e.id, null);
    setBusy(false);
    toaster.toast({
      title: "SaveWaypoint",
      body: r.ok ? `Restored ${e.name}` : `Error: ${r.error}`,
    });
  };

  const doTest = async (e: Entry) => {
    const r = await testEntry(e.id);
    toaster.toast({
      title: e.name,
      body: r.ok
        ? `✅ readable · ${r.restorable ? "restorable" : "read-only!"} · ${humanSize(r.archive_size)}`
        : `⚠️ ${r.error}`,
    });
  };

  // --- Not connected: setup + device flow -----------------------------------
  if (!status) {
    return <PanelSection title="SaveWaypoint">Loading…</PanelSection>;
  }

  if (!status.connected) {
    return (
      <PanelSection title="Connect GitHub">
        {device ? (
          <>
            <PanelSectionRow>
              On your phone, open <b>{device.verification_uri}</b> and enter:
            </PanelSectionRow>
            <PanelSectionRow>
              <div style={{ fontSize: "2em", fontWeight: "bold", letterSpacing: "3px", textAlign: "center" }}>
                {device.user_code}
              </div>
            </PanelSectionRow>
            <PanelSectionRow>Waiting for approval…</PanelSectionRow>
          </>
        ) : (
          <>
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
                Saves go to a private repo on your own account. See README to create
                the one-time OAuth App Client ID.
              </span>
            </PanelSectionRow>
          </>
        )}
      </PanelSection>
    );
  }

  // --- Connected: main UI ----------------------------------------------------
  const selectedCount = entries.filter((e) => e.selected).length;
  return (
    <>
      <PanelSection title={`Connected · ${status.login}`}>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={doScan} disabled={busy}>
            {busy ? "Scanning…" : "Scan for saves"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={doBackup} disabled={busy || selectedCount === 0}>
            {`Back up now (${selectedCount})`}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      {entries.length > 0 && (
        <PanelSection title="Detected saves">
          {entries.map((e) => (
            <PanelSectionRow key={e.id}>
              <Focusable style={{ display: "flex", flexDirection: "column", width: "100%" }}>
                <ToggleField
                  label={`${statusDot(e.status)} ${e.name}`}
                  description={`${e.source} · ${e.file_count} files · ${humanSize(e.size_bytes)}`}
                  checked={e.selected}
                  disabled={e.status === "red"}
                  onChange={(v) => toggle(e.id, v)}
                />
                <div style={{ display: "flex", gap: "6px" }}>
                  <ButtonItem layout="below" onClick={() => doTest(e)}>
                    Test
                  </ButtonItem>
                  <ButtonItem layout="below" onClick={() => doRestore(e)}>
                    Restore
                  </ButtonItem>
                </div>
              </Focusable>
            </PanelSectionRow>
          ))}
        </PanelSection>
      )}

      <PanelSection title="Account">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              await disconnect();
              refresh();
            }}
          >
            Disconnect
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: "SaveWaypoint",
  titleView: <div className={staticClasses.Title}>SaveWaypoint</div>,
  content: <Content />,
  icon: <FaMapPin />,
}));
