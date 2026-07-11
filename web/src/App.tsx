import { useState } from "react";

import { AuthoringPage } from "./features/authoring/AuthoringPage";
import { ExecutionPage } from "./features/execution/ExecutionPage";

type Surface = "authoring" | "execution";

export function App() {
  const [surface, setSurface] = useState<Surface>("authoring");

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Computer-use automation</p>
          <h1>Automation Foundry</h1>
        </div>
        <nav aria-label="Primary navigation">
          <button
            className={surface === "authoring" ? "active" : ""}
            onClick={() => setSurface("authoring")}
            type="button"
          >
            Automations
          </button>
          <button
            className={surface === "execution" ? "active" : ""}
            onClick={() => setSurface("execution")}
            type="button"
          >
            Run
          </button>
        </nav>
      </header>
      <main>{surface === "authoring" ? <AuthoringPage /> : <ExecutionPage />}</main>
    </div>
  );
}
