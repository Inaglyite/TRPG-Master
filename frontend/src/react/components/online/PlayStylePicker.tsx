import type { KeyboardEvent } from "react";

import type { KeeperMode } from "../../../protocol/structured";

const STYLES = [
  {
    id: "legacy",
    label: "经典 AI 叙事",
    description: "自由说话，由 AI 守秘人推进故事",
    mark: "书",
  },
  {
    id: "human",
    label: "人类主持",
    description: "自己主持，不需要模型或 API Key",
    mark: "人",
  },
  {
    id: "assisted",
    label: "AI 辅助主持",
    description: "AI 给出草稿，由主持确认后执行",
    mark: "笔",
  },
  {
    id: "agent",
    label: "AI 主持",
    description: "按钮提交行动，由 AI 判断并执行",
    mark: "灯",
  },
] as const;

type PlayStyle = (typeof STYLES)[number]["id"];

/** The visible play style is translated into the existing creation contract. */
export function PlayStylePicker({
  structured,
  keeperMode,
  disabled,
  solo = false,
  onChange,
}: {
  structured: boolean;
  keeperMode: KeeperMode;
  disabled: boolean;
  solo?: boolean;
  onChange: (value: { structured: boolean; keeperMode: KeeperMode }) => void;
}) {
  const selected: PlayStyle = structured ? keeperMode : "legacy";
  const choose = (id: PlayStyle) => {
    onChange({
      structured: id !== "legacy",
      keeperMode: id === "legacy" ? keeperMode : id,
    });
  };
  const navigate = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const delta =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (!delta || disabled) return;
    event.preventDefault();
    const next = (index + delta + STYLES.length) % STYLES.length;
    choose(STYLES[next].id);
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLButtonElement>('[role="radio"]')
      [next]?.focus();
  };

  return (
    <fieldset className="play-style-picker" disabled={disabled}>
      <legend>游玩方式</legend>
      <div
        className="play-style-options"
        role="radiogroup"
        aria-label="游玩方式"
      >
        {STYLES.map((style, index) => (
          <button
            key={style.id}
            type="button"
            role="radio"
            aria-label={style.label}
            aria-checked={selected === style.id}
            tabIndex={selected === style.id ? 0 : -1}
            disabled={disabled}
            className="play-style-option"
            onClick={() => choose(style.id)}
            onKeyDown={(event) => navigate(event, index)}
          >
            <span className="play-style-mark" aria-hidden="true">
              {style.mark}
            </span>
            <strong>{style.label}</strong>
            <span>{style.description}</span>
          </button>
        ))}
      </div>
      <p className="play-style-explanation" role="note">
        {selected === "human"
          ? "不调用模型。主持通过工作台发布叙事、分发线索、请求检定和调整场景。"
          : selected === "assisted"
            ? "需要自行配置模型与 API Key；AI 草稿只有经主持批准后才会执行。"
            : selected === "agent"
              ? "需要自行配置模型与 API Key；主持可以接管或重试暂停的行动。"
              : "需要自行配置模型与 API Key，沿用经典文字叙事流程。"}
      </p>
      {structured && (
        <p className="play-style-limitation">
          当前结构化模式支持调查与社交，尚无完整战斗和结局结算。
          {solo &&
            keeperMode === "human" &&
            " 单人人类主持由你兼任调查员和守秘人，主持资料也会对你可见。"}
        </p>
      )}
    </fieldset>
  );
}
