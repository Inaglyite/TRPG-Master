/** Small line emblems for the secondary archive actions. */
export function StartMenuIcon({
  kind,
}: {
  kind: "modules" | "characters" | "import" | "workshop";
}) {
  return (
    <svg
      className="start-menu-icon start-menu-line-icon"
      data-menu-icon={kind}
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
      ) : kind === "import" ? (
        <>
          <path d="M10 2v10m-3-3 3 3 3-3M3 12v5h14v-5" />
        </>
      ) : kind === "workshop" ? (
        <>
          <path d="m12 3 5 5-9 9H3v-5zM10 5l5 5M3 17l4-1-3-3" />
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
