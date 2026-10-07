import { ReactNode } from "react";
import { RunEvent } from "./api";
import { Chip, time } from "./ui";

type Row = { tone: string; actor: string; title: ReactNode; body?: ReactNode };

function kv(label: string, value: ReactNode) {
  return (
    <div className="kv">
      <span>{label}</span>
      <div>{value}</div>
    </div>
  );
}

/** Turn one logged event into a line a reviewer can read without knowing the schema. */
function describe(e: RunEvent): Row | null {
  switch (e.type) {
    case "discovery_started":
      return { tone: "info", actor: "system", title: "Discovery started", body: <>{kv("Goal", e.goal)}{kv("Model", <span className="mono">{e.model}</span>)}</> };
    case "replay_started":
      return {
        tone: "info", actor: "system",
        title: <>Replay started{e.dry_run && " (dry run: stops before any irreversible step)"}</>,
        body: kv("Artifact", <span className="mono">{e.capability}@{e.version} · {e.status}</span>),
      };
    case "session_opened":
      return {
        tone: "neutral", actor: "system", title: `Session opened on tenant "${e.tenant}"`,
        body: e.overrides?.length ? kv("Tenant overrides", <span className="mono">{e.overrides.join(", ")}</span>) : undefined,
      };
    case "recovery_applied":
      return {
        tone: "warn", actor: "automation", title: `Recovered: ${String(e.name).replace(/_/g, " ")}`,
        body: <span className="muted">{e.then === "restart" ? "Then restarted the flow from its first step." : "Then carried on from the same step."}</span>,
      };
    case "model_turn": {
      const call = e.calls?.[0];
      if (!call) return { tone: "model", actor: "model", title: "Model replied without acting", body: e.said };
      const { reason, ref, ...rest } = call.input;
      return {
        tone: "model", actor: "model",
        title: <>Model chose <span className="mono">{call.tool}</span>{ref && <> on <span className="mono">{ref}</span></>}</>,
        body: (
          <>
            {(reason ?? rest.summary ?? rest.question) && <div>{reason ?? rest.summary ?? rest.question}</div>}
            {Object.entries(rest).filter(([k]) => !["summary", "question"].includes(k)).map(([k, v]) => kv(k, <span className="mono">{String(v)}</span>))}
            {e.calls.length > 1 && <div className="muted small">It asked for {e.calls.length} actions at once; only the first is taken per turn.</div>}
          </>
        ),
      };
    }
    case "tool_result":
      return { tone: e.is_error ? "bad" : "ok", actor: "automation", title: e.is_error ? "Action refused or failed" : "Action carried out", body: e.result !== "ok" ? e.result : undefined };
    case "step_done":
      return {
        tone: "ok", actor: "automation",
        title: <>Step <span className="mono">{e.step}</span>: {e.action} <span className="mono">{e.target}</span> {e.risk === "irreversible" && <Chip value="irreversible" />}</>,
      };
    case "slow_wait":
      return { tone: "warn", actor: "automation", title: `Slow response absorbed: waited ${e.ms} ms before ${e.before_step}` };
    case "policy_blocked":
      return { tone: "bad", actor: "policy", title: "Blocked by policy", body: e.detail };
    case "failure":
      return {
        tone: "bad", actor: "automation", title: <>{e.kind} before step <span className="mono">{e.step}</span></>,
        body: <>{kv("Expected", e.expected)}{kv("Observed", e.observed)}</>,
      };
    case "intervention_requested":
      return {
        tone: "human", actor: "system", title: e.kind === "approval" ? "Approval requested from a human" : "Help requested from a human",
        body: <>{kv("Reason", e.reason)}{kv("Options offered", <span className="mono">{e.options.join(" / ")}</span>)}</>,
      };
    case "intervention_unrouted":
      return { tone: "neutral", actor: "system", title: "No operator was attached to this run, so the request went nowhere" };
    case "control_transferred":
      return e.to === "human"
        ? { tone: "human", actor: "system", title: "Control handed to the human. Automation is locked out of the session." }
        : { tone: "human", actor: "human", title: <>Human answered <span className="mono">{e.decision}</span>. Control returned to automation.</>, body: <span className="muted">Decided by {e.by}</span> };
    case "human_actions":
      return {
        tone: "human", actor: "human", title: `Human actions recorded: ${e.actions.length}`,
        body: e.actions.length ? (
          <ul className="plain">
            {e.actions.map((a: any, i: number) => (
              <li key={i}><span className="mono">{a.kind}</span> {a.label || a.near || a.tag} <span className="muted">in frame {a.frame || "top"} ({a.path})</span></li>
            ))}
          </ul>
        ) : <span className="muted">The operator decided without touching the application.</span>,
      };
    case "run_finished":
      return {
        tone: e.status === "success" ? "ok" : e.status === "failed" ? "bad" : "warn", actor: "system",
        title: <>Run finished: <Chip value={e.status} /></>,
        body: e.reason ?? (e.error ? <span className="mono">{e.error}</span> : undefined),
      };
    default:
      return null; // run_started and control_history are shown in the header
  }
}

export function Timeline({ events }: { events: RunEvent[] }) {
  return (
    <ol className="timeline">
      {events.map((e) => {
        const row = describe(e);
        return row && (
          <li key={e.seq} className={`tl tl-${row.tone}`}>
            <span className="tl-time mono">{time(e.ts)}</span>
            <span className="tl-actor">{row.actor}</span>
            <div className="tl-body">
              <div className="tl-title">{row.title}</div>
              {row.body && <div className="tl-detail">{row.body}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
