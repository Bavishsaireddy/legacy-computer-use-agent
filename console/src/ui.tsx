import { ReactNode } from "react";

const TONES: Record<string, string> = {
  success: "ok", approved: "ok", committed: "ok", verified: "ok", safe: "neutral",
  business_outcome: "info", draft: "neutral", none: "neutral", running: "info",
  failed: "bad", irreversible: "warn", unknown: "warn", needs_human: "warn",
};

const LABELS: Record<string, string> = {
  business_outcome: "business outcome", needs_human: "needs human",
};

export function Chip({ value, tone }: { value: string; tone?: string }) {
  return <span className={`chip chip-${tone ?? TONES[value] ?? "neutral"}`}>{LABELS[value] ?? value}</span>;
}

export function Card({ title, aside, children }: { title?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || aside) && (
        <header className="card-head">
          <h2>{title}</h2>
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

export function PageHead({ title, lead }: { title: string; lead: ReactNode }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      <p>{lead}</p>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function time(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/** "09-replay-human-handoff" -> ["09", "Replay human handoff"] */
export function splitLabel(label: string): [string, string] {
  const m = label.match(/^(\d+)-(.*)$/);
  const words = (m ? m[2] : label).replace(/-/g, " ");
  return [m ? m[1] : "", words.charAt(0).toUpperCase() + words.slice(1)];
}

export function go(hash: string) {
  window.location.hash = hash;
}
