import { useEffect } from "react";

import { connect } from "../ws";
import { GameShell } from "./GameShell";

export function App() {
  useEffect(() => {
    connect();
  }, []);

  return <GameShell />;
}
