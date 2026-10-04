import type { ComponentPropsWithRef } from "react";

type Props = ComponentPropsWithRef<"section"> & {
  variant?: "portrait" | "wide";
};

/** Decorative nine-slice skin only. Content, semantics and actions remain DOM. */
export function ArchiveFolderPanel({
  variant = "portrait",
  className = "",
  children,
  ...props
}: Props) {
  return (
    <section
      {...props}
      className={`archive-folder-panel archive-folder-panel--${variant} ${className}`.trim()}
    >
      {children}
    </section>
  );
}
