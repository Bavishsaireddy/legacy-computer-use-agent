import { Capability, Locator, useApi } from "../api";
import { Card, Chip, Empty, PageHead, go } from "../ui";

const VERBS: Record<string, string> = { click: "Click", type: "Type into", select: "Choose in", extract: "Read" };

function locatorText(l: Locator): string {
  const frame = l.frame?.length ? `frame ${l.frame.join("/")} › ` : "";
  if (l.kind === "near_text") return `${frame}first ${l.role} at text "${l.text}"`;
  const parts = [l.role];
  if (l.name) parts.push(`named "${l.name}"`);
  if (l.row) parts.push(`in the row captioned "${l.row}"`);
  if (l.nth !== undefined) parts.push(`(position ${l.nth + 1})`);
  return frame + parts.join(" ");
}

export function Capabilities({ selected }: { selected?: string }) {
  const { data: caps } = useApi<Capability[]>("/api/capabilities");
  if (!caps) return null;
  if (!caps.length) return <Empty>No capabilities saved yet. Run a discovery first.</Empty>;
  const cap = caps.find((c) => c.capability === selected) ?? caps[0];
  const prov = cap.provenance ?? {};

  return (
    <>
      <PageHead
        title="Capabilities"
        lead="A capability is the artifact discovery produces: a reviewable contract an agent can call, kept separate from the model transcript that found it."
      />
      <div className="tabs">
        {caps.map((c) => (
          <button key={c.capability} className={c === cap ? "active" : ""} onClick={() => go(`/capabilities/${c.capability}`)}>
            {c.capability}
          </button>
        ))}
      </div>

      <Card
        title={<span className="mono">{cap.capability}</span>}
        aside={<span className="row-gap"><span className="mono muted">v{cap.version}</span><Chip value={cap.status} /></span>}
      >
        <p className="lead">{cap.description}</p>
        <dl className="facts">
          <div><dt>Runs on</dt><dd className="mono">{cap.app.product} {cap.app.versions}</dd></div>
          <div><dt>Discovered by</dt><dd className="mono">{prov.discovered_by ?? "—"}</dd></div>
          <div><dt>Verified by replay</dt><dd>{prov.verified ? <Chip value="verified" /> : <Chip value="not verified" tone="warn" />}</dd></div>
          <div><dt>File</dt><dd className="mono">{cap.file}</dd></div>
        </dl>
      </Card>

      <div className="two">
        <Card title="Inputs the caller supplies">
          <table className="table">
            <thead><tr><th>Name</th><th>Type</th><th>Must match</th><th>Sensitivity</th></tr></thead>
            <tbody>
              {Object.entries(cap.params ?? {}).map(([name, p]) => (
                <tr key={name}>
                  <td className="mono">{name}</td><td>{p.type}</td><td className="mono">{p.pattern ?? "—"}</td><td><Chip value={p.sensitivity} tone="neutral" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="What the caller gets back">
          <table className="table">
            <thead><tr><th>Name</th><th>Type</th><th>Sensitivity</th></tr></thead>
            <tbody>
              {Object.entries(cap.outputs ?? {}).map(([name, o]) => (
                <tr key={name}><td className="mono">{name}</td><td>{o.type}</td><td><Chip value={o.sensitivity} tone="neutral" /></td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <Card title="Flow" aside={<span className="muted small">A step is complete when the next step's control appears</span>}>
        <ol className="flow">
          {cap.steps.map((s) => (
            <li key={s.id} className={s.risk === "irreversible" ? "risky" : ""}>
              <span className="flow-id mono">{s.id}</span>
              <div>
                <div className="flow-line">
                  <strong>{VERBS[s.action] ?? s.action}</strong> {cap.targets[s.target]?.description ?? s.target}
                  {s.value && <span className="mono pill">{s.value}</span>}
                  {s.output && <span className="mono pill">→ {s.output}</span>}
                  {s.risk === "irreversible" && <Chip value="irreversible" />}
                </div>
                {s.note && <div className="muted small">{s.note}</div>}
                {s.outcomes?.map((o) => (
                  <div className="outcome" key={o.name}>
                    <Chip value={o.name} tone="info" /> <span className="small">may follow this step: {o.description}</span>
                  </div>
                ))}
              </div>
            </li>
          ))}
          <li className="done">
            <span className="flow-id mono">end</span>
            <div><strong>Success when</strong> {cap.targets[cap.success]?.description ?? cap.success} is on screen</div>
          </li>
        </ol>
      </Card>

      <Card title="How each control is found" aside={<span className="muted small">Tried in order; each must match exactly one control</span>}>
        <table className="table">
          <thead><tr><th>Target</th><th>Primary: accessibility tree</th><th>Fallback: visible text</th></tr></thead>
          <tbody>
            {Object.entries(cap.targets).map(([name, t]) => (
              <tr key={name}>
                <td className="mono">{name}</td>
                <td>{locatorText(t.locators[0])}</td>
                <td className="muted">{t.locators[1] ? locatorText(t.locators[1]) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {!!prov.notes?.length && (
        <Card title="Provenance notes">
          <ul className="rules">
            {[...(prov.verification ?? []).map((v) => `verification run ${v}`), ...prov.notes].map((n) => <li key={n}>{n}</li>)}
          </ul>
        </Card>
      )}
    </>
  );
}
