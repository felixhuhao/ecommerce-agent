import type { ReactNode } from "react";

interface AppShellProps {
  sidebar: ReactNode;
  conversation: ReactNode;
  rail: ReactNode;
}

export function AppShell({ sidebar, conversation, rail }: AppShellProps) {
  return (
    <main className="console-shell">
      <aside className="console-sidebar">{sidebar}</aside>
      <section className="console-main">{conversation}</section>
      <aside className="console-rail">{rail}</aside>
    </main>
  );
}
