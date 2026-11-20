import type { ReactNode } from "react";
import { ModuleSelect, type ModuleSelectOption } from "./ModuleSelect";

type Props = {
  title: string;
  subtitle?: string;
  description?: string;
  modules: ModuleSelectOption[];
  activeModule: string;
  selectingDisabled: boolean;
  onSelect: (id: string) => void;
  startLabel: string;
  startDisabled: boolean;
  onStart: () => void;
  continueDisabled?: boolean;
  onContinue: () => void;
  hint?: ReactNode;
};

/** 本地、云端共用同一份开局菜单；选模组、创建世界和读档由各自入口处理。 */
export function SoloStartMenu(props: Props) {
  return (
    <div className="start-menu-main">
      <div className="start-brand">
        <h1 id="start-title" className="fx-glow">
          {props.title}
        </h1>
        {props.subtitle && <div id="start-subtitle">{props.subtitle}</div>}
        {props.description && (
          <div id="start-description">{props.description}</div>
        )}
      </div>
      <div id="module-selector">
        <label id="module-select-label">当前模组</label>
        <div className="module-select-row">
          <ModuleSelect
            options={props.modules}
            value={props.activeModule}
            disabled={props.selectingDisabled}
            labelledBy="module-select-label"
            onSelect={props.onSelect}
          />
        </div>
      </div>
      <nav className="start-menu-actions">
        <button
          id="btn-start"
          className="start-art-button"
          disabled={props.startDisabled}
          onClick={props.onStart}
        >
          <span className="start-art-label">{props.startLabel}</span>
        </button>
        <button
          id="btn-continue"
          className="start-art-button art-plaque"
          disabled={props.continueDisabled}
          onClick={props.onContinue}
        >
          <span className="start-art-label">从存档开始</span>
        </button>
      </nav>
      <div id="start-hint" role="status">
        {props.hint}
      </div>
    </div>
  );
}
