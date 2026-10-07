import { useState } from "react";
import { Intervention, RunSummary, post, useApi } from "../api";
import { Card, Chip, Empty, PageHead, go, splitLabel } from "../ui";

const STEPS = [
  { who: "automation", title: "Automation gets stuck", text: "A control cannot be found, a checkpoint times out, the app shows an error, or an irreversible step needs a decision." },
  { who: "system", title: "A request is raised", text: "It carries the capability, the step, why it stopped, what was expected, a screenshot and the choices on offer." },
  { who: "system", title: "Control moves to the human", text: "The session's lease changes owner. Automation is refused any action until it gets the lease back." },
  { who: "human", title: "The human works in the same session", text: "Same browser, same login, same page. Their clicks and changes are recorded; what they type is not." },
  { who: "automation", title: "Control returns and is re-checked", text: "The human's answer is not taken on trust: automation verifies the screen before it continues." },
];

const OPTIONS: Record<string, { label: string; hint: string; tone: string }> = {
  retry: { label: "Retry the step", hint: "I fixed the screen; run the step again.", tone: "primary" },
  done: { label: "I did this step", hint: "Skip it and verify the result.", tone: "primary" },
  approve: { label: "Approve", hint: "Allow this irreversible action once.", tone: "primary" },
  deny: { label: "Deny", hint: "Do not perform the action.", tone: "plain" },
  abort: { label: "Abort the run", hint: "Stop here and report the failure.", tone: "plain" },
};

function Request({ request }: { request: Intervention }) {
  const [sent, setSent] = useState("");
  const decide = (action: string) => {
    setSent(action);
    post(`/api/interventions/${request.id}`, { action, by: "console-operator" });
  };
  return (
    <div className="request">
      <div className="request-head">
        <Chip value={request.kind === "approval" ? "approval needed" : "automation is stuck"} tone="warn" />
        <span className="mono small">{request.capability}</span>
        <span className="muted small">stopped before step <span className="mono">{request.step}</span></span>
      </div>
      <div className="request-grid">
        <div>
          <div className="kv"><span>Why it stopped</span><div>{request.reason}</div></div>
          {request.expected && <div className="kv"><span>It expected</span><div>{request.expected}</div></div>}
          <div className="callout callout-info small">
            You now control the browser window this run opened. Do what is needed there, then choose below.
          </div>
          <div className="actions">
            {request.options.map((o) => (
              <button key={o} className={`btn btn-${OPTIONS[o]?.tone ?? "plain"}`} disabled={!!sent} onClick={() => decide(o)} title={OPTIONS[o]?.hint}>
                {OPTIONS[o]?.label ?? o}
              </button>
            ))}
          </div>
          {sent && <div className="muted small">Sent "{sent}". Handing control back to automation…</div>}
        </div>
        <img className="shot" src={`/files/${request.screenshot}`} alt="The screen automation stopped on" />
      </div>
    </div>
  );
}

export function Handoff() {
  const { data: waiting } = useApi<Intervention[]>("/api/interventions", 1500);
  const { data: status } = useApi<{ mock_app: boolean; mock_app_url: string }>("/api/status", 4000);
  const { data: runs } = useApi<RunSummary[]>("/api/runs", 3000);
  const [message, setMessage] = useState("");

  const start = async () => {
    setMessage("Starting…");
    const reply = await post<{ ok: boolean; message: string }>("/api/demo/handoff");
    setMessage(reply.message);
  };
  const withHandoff = (runs ?? []).filter((r) => r.handoffs > 0).sort((a, b) => a.label.localeCompare(b.label));
  const latestLocal = (runs ?? []).filter((r) => r.source === "runs").slice(0, 4);

  return (
    <>
      <PageHead
        title="Human in the loop"
        lead="When automation cannot safely proceed, it stops and hands the live session to a person. One lease decides who is in control at any moment, and every transfer is logged."
      />

      <Card title="How control moves">
        <ol className="handoff-steps">
          {STEPS.map((s, i) => (
            <li key={s.title} className={`who-${s.who}`}>
              <span className="step-n">{i + 1}</span>
              <span className="step-who">{s.who}</span>
              <h3>{s.title}</h3>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      </Card>

      <Card
        title="Operator console"
        aside={status && <Chip value={status.mock_app ? "mock app running" : "mock app not running"} tone={status.mock_app ? "ok" : "bad"} />}
      >
        {waiting?.length ? (
          waiting.map((r) => <Request key={r.id} request={r} />)
        ) : (
          <div className="try">
            <div>
              <h3>Try a takeover yourself</h3>
              <p>
                This starts a real replay and makes the app answer with an error page, so automation gets stuck. A browser window opens;
                the request appears here. In that window, click <strong>Member Lookup</strong> in the left menu to put the app back on
                the right screen, then choose <strong>Retry the step</strong> here.
              </p>
              {!status?.mock_app && (
                <p className="mono small">Start the app first: uv run python -m mockapp.server</p>
              )}
            </div>
            <div className="try-action">
              <button className="btn btn-primary" onClick={start} disabled={!status?.mock_app}>Start a stuck run</button>
              {message && <div className="muted small">{message}</div>}
            </div>
          </div>
        )}
        {!!latestLocal.length && (
          <>
            <h3 className="sub">Latest runs on this machine</h3>
            <table className="table hover">
              <tbody>
                {latestLocal.map((r) => (
                  <tr key={r.id} onClick={() => go(`/evidence/${r.id}`)}>
                    <td className="mono small">{r.id}</td><td><Chip value={r.status} /></td>
                    <td>{r.handoffs ? `${r.handoffs} handoff${r.handoffs > 1 ? "s" : ""}` : "no handoff"}</td>
                    <td className="mono small">{r.error_kind ?? r.outcome ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Card>

      <Card title="Handoffs in the recorded evidence">
        {withHandoff.length ? (
          <table className="table hover">
            <thead><tr><th>Scenario</th><th>Type</th><th>Result</th><th>Side effects</th><th>What it shows</th></tr></thead>
            <tbody>
              {withHandoff.filter((r) => r.source === "evidence").map((r) => (
                <tr key={r.id} onClick={() => go(`/evidence/${r.id}`)}>
                  <td>{splitLabel(r.label).join(" ")}</td>
                  <td>{r.kind}</td>
                  <td><Chip value={r.status} /></td>
                  <td><Chip value={r.side_effects} /></td>
                  <td className="small">
                    {r.kind === "discovery" ? "The model reached an irreversible button and had to wait for a human approval."
                      : r.status === "success" ? "An operator fixed the screen and automation resumed and finished."
                      : "A request was raised but no operator was attached, so the run stopped with its evidence."}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty>No handoffs recorded yet.</Empty>}
      </Card>
    </>
  );
}
