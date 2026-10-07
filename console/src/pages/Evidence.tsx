import { useState } from "react";
import { RunDetail, RunSummary, useApi } from "../api";
import { Timeline } from "../Timeline";
import { Card, Chip, Empty, PageHead, go, splitLabel, time } from "../ui";

const FILTERS = ["all", "success", "business_outcome", "failed", "needs_human"];

function ending(r: RunSummary): string {
  return r.outcome ?? r.error_kind ?? (r.dry_run ? "dry run" : "");
}

function RunTable({ runs }: { runs: RunSummary[] }) {
  return (
    <table className="table hover">
      <thead>
        <tr>
          <th>Scenario</th><th>Type</th><th>Capability</th><th>Result</th><th>Detail</th><th>Side effects</th><th className="num">Handoffs</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => {
          const [n, words] = splitLabel(r.label || r.id);
          return (
            <tr key={r.id} onClick={() => go(`/evidence/${r.id}`)}>
              <td>{n && <span className="mono muted">{n} </span>}{r.label ? words : <span className="mono small">{r.id}</span>}</td>
              <td>{r.kind === "discovery" ? <Chip value="LLM discovery" tone="model" /> : <Chip value="replay" tone="neutral" />}</td>
              <td className="mono small">{r.name}</td>
              <td><Chip value={r.status} /></td>
              <td className="mono small">{ending(r) || "—"}</td>
              <td>{r.side_effects !== "none" ? <Chip value={r.side_effects} /> : <span className="muted">none</span>}</td>
              <td className="num">{r.handoffs || "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Detail({ id }: { id: string }) {
  const { data: run, error } = useApi<RunDetail>(`/api/runs/${id}`, 3000);
  if (error) return <Empty>Run not found.</Empty>;
  if (!run) return null;
  const [n, words] = splitLabel(run.label || run.id);
  const result = run.result;
  const shots = run.files.filter((f) => f.endsWith(".png"));
  const snaps = run.files.filter((f) => f.endsWith(".txt"));
  const transfers = run.events.find((e) => e.type === "control_history")?.transfers ?? [];

  return (
    <>
      <a className="back" href="#/evidence">← All evidence</a>
      <PageHead title={`${n ? n + " · " : ""}${words}`} lead={<span className="mono small">{run.path}</span>} />

      <Card title="Result" aside={<Chip value={run.status} />}>
        <dl className="facts">
          <div><dt>Type</dt><dd>{run.kind === "discovery" ? `LLM discovery (${run.model})` : "Deterministic replay, no model"}</dd></div>
          <div><dt>Capability</dt><dd className="mono">{run.name}</dd></div>
          <div><dt>Side effects</dt><dd><Chip value={run.side_effects} /></dd></div>
          <div><dt>Recoveries</dt><dd className="mono">{run.recoveries.join(", ") || "—"}</dd></div>
        </dl>
        {result?.outcome && (
          <div className="callout callout-info">
            <strong>{result.outcome.name}</strong> {result.outcome.description}
            <div className="mono small">{result.outcome.message}</div>
          </div>
        )}
        {result?.error && (
          <div className="callout callout-bad">
            <strong>{result.error.kind}</strong> at step <span className="mono">{result.error.step ?? "—"}</span> ({result.error.phase}) ·{" "}
            {result.error.retryable ? "safe to retry" : "not safe to retry automatically"}
            <div className="kv"><span>Expected</span><div>{result.error.expected}</div></div>
            <div className="kv"><span>Observed</span><div>{result.error.observed}</div></div>
          </div>
        )}
        {result && !!Object.keys(result.outputs ?? {}).length && (
          <div className="callout callout-ok">
            <strong>Outputs</strong> <span className="muted small">(redacted in the saved copy; the caller received the real values)</span>
            {Object.entries(result.outputs).map(([k, v]) => <div className="mono small" key={k}>{k}: {String(v)}</div>)}
          </div>
        )}
        {result?.warnings?.map((w: string) => <div className="callout callout-warn small" key={w}>{w}</div>)}
      </Card>

      {!!transfers.length && (
        <Card title="Who was in control">
          <table className="table">
            <thead><tr><th>Time</th><th>From</th><th>To</th><th>Reason</th></tr></thead>
            <tbody>
              {transfers.map((t: any) => (
                <tr key={t.ts}><td className="mono">{time(t.ts)}</td><td>{t.from}</td><td><strong>{t.to}</strong></td><td>{t.reason}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title="Timeline" aside={<span className="muted small">{run.events.length} logged events</span>}>
        <Timeline events={run.events} />
      </Card>

      {!!shots.length && (
        <Card title="Screenshot at the point of failure" aside={<span className="muted small">Controls holding sensitive values are masked</span>}>
          {shots.map((f) => <img className="shot" key={f} src={`/files/${run.path}/${f}`} alt={`Screenshot ${f}`} />)}
        </Card>
      )}
      {!!snaps.length && (
        <Card title="Other evidence files">
          <ul className="plain">
            {snaps.map((f) => <li key={f}><a href={`/files/${run.path}/${f}`} target="_blank" rel="noreferrer">{f}</a> <span className="muted">redacted accessibility snapshot</span></li>)}
          </ul>
        </Card>
      )}
    </>
  );
}

export function Evidence({ selected }: { selected?: string }) {
  const { data: runs } = useApi<RunSummary[]>("/api/runs", 5000);
  const [filter, setFilter] = useState("all");
  if (selected) return <Detail id={selected} />;
  if (!runs) return null;

  const match = (r: RunSummary) => filter === "all" || r.status === filter;
  const recorded = runs.filter((r) => r.source === "evidence" && match(r)).sort((a, b) => (a.label + a.id).localeCompare(b.label + b.id));
  const local = runs.filter((r) => r.source === "runs" && match(r));

  return (
    <>
      <PageHead
        title="Evidence"
        lead="Every run writes a structured log of what happened and why. These are the recorded runs from the end-to-end demo; select one to see its timeline, result and failure evidence."
      />
      <div className="tabs">
        {FILTERS.map((f) => (
          <button key={f} className={f === filter ? "active" : ""} onClick={() => setFilter(f)}>
            {f.replace("_", " ")} <span className="muted">{runs.filter((r) => f === "all" || r.status === f).length}</span>
          </button>
        ))}
      </div>
      <Card title="Recorded demo runs" aside={<span className="mono muted small">evidence/</span>}>
        {recorded.length ? <RunTable runs={recorded} /> : <Empty>No recorded runs with this result.</Empty>}
      </Card>
      {!!local.length && (
        <Card title="Runs on this machine" aside={<span className="mono muted small">runs/</span>}>
          <RunTable runs={local} />
        </Card>
      )}
    </>
  );
}
