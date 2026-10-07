import { useEffect, useState } from "react";
import { Intervention, useApi } from "./api";
import { Capabilities } from "./pages/Capabilities";
import { Evidence } from "./pages/Evidence";
import { Handoff } from "./pages/Handoff";
import { Overview } from "./pages/Overview";

const PAGES = [
  { key: "overview", label: "Overview" },
  { key: "capabilities", label: "Capabilities" },
  { key: "evidence", label: "Evidence" },
  { key: "handoff", label: "Human in the loop" },
];

function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function App() {
  const [page = "overview", arg] = useRoute();
  const { data: waiting } = useApi<Intervention[]>("/api/interventions", 2000);

  return (
    <div className="shell">
      <nav className="side">
        <div className="brand">
          <strong>cua</strong>
          <span>record once, replay many</span>
        </div>
        {PAGES.map((p) => (
          <a key={p.key} href={`#/${p.key}`} className={p.key === page ? "active" : ""}>
            {p.label}
            {p.key === "handoff" && !!waiting?.length && <span className="badge">{waiting.length}</span>}
          </a>
        ))}
        <div className="side-foot">Target: HarborCore mock back office</div>
      </nav>
      <main className="main">
        {page === "overview" && <Overview />}
        {page === "capabilities" && <Capabilities selected={arg} />}
        {page === "evidence" && <Evidence selected={arg} />}
        {page === "handoff" && <Handoff />}
      </main>
    </div>
  );
}
