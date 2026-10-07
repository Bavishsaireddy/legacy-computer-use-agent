import { Capability, RunSummary, useApi } from "../api";
import { Card, Chip, PageHead } from "../ui";

function count<T>(items: T[], test: (item: T) => boolean): number {
  return items.filter(test).length;
}

function names(values: (string | null)[]): string {
  return [...new Set(values.filter(Boolean))].join(", ");
}

export function Overview() {
  const { data: all } = useApi<RunSummary[]>("/api/runs");
  const { data: caps } = useApi<Capability[]>("/api/capabilities");
  const runs = (all ?? []).filter((r) => r.source === "evidence");
  const discoveries = runs.filter((r) => r.kind === "discovery");
  const replays = runs.filter((r) => r.kind === "replay");
  const model = names(discoveries.map((r) => r.model)) || "an LLM";

  const stages = [
    {
      n: "1", title: "Discover", who: `Model: ${model}`, figure: discoveries.length, unit: "discovery runs",
      text: "An LLM is given a goal and drives the live application once, reading the screen as an accessibility tree and choosing each click and keystroke.",
    },
    {
      n: "2", title: "Compile", who: "No model", figure: caps?.length ?? 0, unit: "capabilities saved",
      text: "The successful run becomes a typed, versioned file: inputs, outputs, steps, how each control is found, and how success is recognised.",
    },
    {
      n: "3", title: "Replay", who: "No model", figure: replays.length, unit: "replay runs",
      text: "The file is executed deterministically with new inputs. Every step is verified, and the result says exactly which kind of ending it was.",
    },
    {
      n: "4", title: "Escalate", who: "Human operator", figure: count(runs, (r) => r.handoffs > 0), unit: "runs that asked for a human",
      text: "When automation cannot safely continue, it pauses, hands the same live session to a person, records what they do, and re-verifies before going on.",
    },
  ];

  const endings = [
    { status: "success", meaning: "The flow completed and its checkpoint was verified; typed outputs are returned.", detail: "" },
    {
      status: "business_outcome", meaning: "A legitimate answer that is not a success, reported by name instead of as an error.",
      detail: names(replays.filter((r) => r.status === "business_outcome").map((r) => r.outcome)),
    },
    {
      status: "failed", meaning: "A hard failure, with the step, what was expected, what was seen, and a screenshot.",
      detail: names(replays.filter((r) => r.status === "failed").map((r) => r.error_kind)),
    },
    {
      status: "needs_human", meaning: "Stopped in a state a person must look at, for example an irreversible step whose result is unknown.",
      detail: names(replays.filter((r) => r.status === "needs_human").map((r) => r.error_kind)),
    },
  ];

  return (
    <>
      <PageHead
        title="What this system does"
        lead="It lets an AI agent operate legacy back-office software that has no API. A model works out how to do a task once; from then on the task runs as a checked, repeatable script with no model involved."
      />

      <div className="stages">
        {stages.map((s) => (
          <div className="stage" key={s.n}>
            <div className="stage-top">
              <span className="stage-n">{s.n}</span>
              <h3>{s.title}</h3>
              <span className="muted small">{s.who}</span>
            </div>
            <p>{s.text}</p>
            <div className="stage-figure">
              <strong>{s.figure}</strong> {s.unit}
            </div>
          </div>
        ))}
      </div>

      <Card title="How the recorded replays ended" aside={<a href="#/evidence">Browse all evidence</a>}>
        <table className="table">
          <thead>
            <tr>
              <th>Ending</th>
              <th className="num">Runs</th>
              <th>What it means</th>
              <th>Seen in evidence</th>
            </tr>
          </thead>
          <tbody>
            {endings.map((e) => (
              <tr key={e.status}>
                <td><Chip value={e.status} /></td>
                <td className="num">{count(replays, (r) => r.status === e.status)}</td>
                <td>{e.meaning}</td>
                <td className="mono small">{e.detail || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Saved capabilities" aside={<a href="#/capabilities">Open</a>}>
        <table className="table">
          <thead>
            <tr>
              <th>Capability</th>
              <th>Version</th>
              <th>Status</th>
              <th className="num">Steps</th>
              <th>Inputs</th>
              <th>Returns</th>
            </tr>
          </thead>
          <tbody>
            {(caps ?? []).map((c) => (
              <tr key={c.capability}>
                <td><a className="mono" href={`#/capabilities/${c.capability}`}>{c.capability}</a></td>
                <td className="mono">{c.version}</td>
                <td><Chip value={c.status} /></td>
                <td className="num">{c.steps.length}</td>
                <td className="mono small">{Object.keys(c.params ?? {}).join(", ")}</td>
                <td className="mono small">{Object.keys(c.outputs ?? {}).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Rules that hold in every run">
        <ul className="rules">
          <li><strong>Allowlist.</strong> The session can only reach permitted pages and action types; anything else is blocked at the network edge.</li>
          <li><strong>Irreversible actions.</strong> They need a human approval during discovery, an approved artifact and the caller's confirm in replay, and are never retried.</li>
          <li><strong>Sensitive data.</strong> Credentials never reach the model. Inputs, outputs and SSN-shaped values are redacted from every log, snapshot and saved result shown here.</li>
        </ul>
      </Card>
    </>
  );
}
