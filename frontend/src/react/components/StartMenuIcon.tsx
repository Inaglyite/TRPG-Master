/** Small line emblems for the secondary archive actions. */
export function StartMenuIcon({ kind }: { kind: "modules" | "characters" }) {
  return (
    <svg
      className="start-menu-icon start-menu-line-icon"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {kind === "modules" ? (
        <>
          <path d="M3 3.5h14v13H3zM3 9.5h14M8 6.5h4M8 12.5h4" />
          <path d="M5 16.5v1M15 16.5v1" />
        </>
      ) : (
        <>
          <rect x="2.5" y="3" width="15" height="14" rx="1.5" />
          <circle cx="7.5" cy="7.5" r="2" />
          <path d="M4.5 14v-1a3 3 0 0 1 6 0v1M13 7h2M13 10h2M13 13h2" />
        </>
      )}
    </svg>
  );
}
