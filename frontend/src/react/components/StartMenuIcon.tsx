import charactersIcon from "../../assets/ui/menu-icon-characters-v1.png";
import exitIcon from "../../assets/ui/menu-icon-exit-v1.png";
import importIcon from "../../assets/ui/menu-icon-import-v1.png";
import modulesIcon from "../../assets/ui/menu-icon-modules-v1.png";
import settingsIcon from "../../assets/ui/menu-icon-settings-v1.png";
import workshopIcon from "../../assets/ui/menu-icon-workshop-v1.png";

const icons = {
  characters: charactersIcon,
  exit: exitIcon,
  import: importIcon,
  modules: modulesIcon,
  settings: settingsIcon,
  workshop: workshopIcon,
};

/** Decorative PNG emblems; the button retains its own accessible label. */
export function StartMenuIcon({ kind }: { kind: keyof typeof icons }) {
  return (
    <img
      className="start-menu-icon"
      data-menu-icon={kind}
      src={icons[kind]}
      alt=""
      aria-hidden="true"
      draggable={false}
    />
  );
}
