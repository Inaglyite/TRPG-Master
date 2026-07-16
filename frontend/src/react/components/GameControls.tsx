import { useEffect, useRef, useState } from "react";

import { sendAction, sendDecisionReply, sendSuggestReply } from "../../options";
import { confirmEnding } from "../../panels";
import { useAppStore, type EndingProposal } from "../../state/app-store";

export function GameControls() {
  const enabled = useAppStore((state) => state.inputEnabled);
  const placeholder = useAppStore((state) => state.inputPlaceholder);
  const choices = useAppStore((state) => state.choices);
  const ending = useAppStore((state) => state.ending);
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (enabled) inputRef.current?.focus();
  }, [enabled]);

  const submit = () => {
    const action = text.trim();
    if (!enabled || !action) return;
    setText("");
    void sendAction(action);
  };

  return (
    <div id="bottom">
      <div id="options-bar">
        {ending ? (
          <>
            <button
              id="btn-end-confirm"
              className="opt-btn end-confirm"
              onClick={() => void confirmEnding(ending)}
            >
              {ending.ending_type === "good"
                ? "🏆"
                : ending.ending_type === "bad"
                  ? "💀"
                  : "🌫"}{" "}
              确认结束 —— {ending.title}
            </button>
            <button
              id="btn-end-continue"
              className="opt-btn free end-continue"
              onClick={() => void sendAction("继续探索")}
            >
              🔄 继续探索
            </button>
          </>
        ) : (
          choices.map((choice, index) => (
            <button
              key={`${choice.label}-${index}`}
              className={`opt-btn${choice.isFree ? " free" : ""}`}
              onClick={() => void sendAction(choice.label)}
            >
              {index + 1}. {choice.label}
            </button>
          ))
        )}
      </div>
      <div id="input-bar">
        <input
          ref={inputRef}
          id="user-input"
          type="text"
          value={text}
          placeholder={placeholder}
          disabled={!enabled}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") submit();
          }}
        />
        <button id="btn-send" disabled={!enabled} onClick={submit}>
          ⏎
        </button>
      </div>
    </div>
  );
}

const dangerousDecisionIds = new Set([
  "confirm_violence",
  "confirm_threat",
  "fight_back",
  "no_defense",
]);

export function DecisionModal() {
  const dialog = useAppStore((state) => state.dialog);
  if (!dialog) return <div id="modal-overlay" className="hidden" />;

  return (
    <div id="modal-overlay">
      <div id="modal-box" role="dialog" aria-modal="true">
        <div id="modal-text">
          {dialog.kind === "suggest" ? (
            <>
              <div className="suggest-desc">{dialog.description}</div>
              <div className="suggest-roll">
                <b>{dialog.skill}</b>（{dialog.attribute}） — 难度：
                {dialog.dc_label}（DC {String(dialog.dc ?? "")}）
              </div>
            </>
          ) : (
            <>
              <div className="decision-title">
                {dialog.title || "需要你做出决定"}
              </div>
              <div className="suggest-desc">{dialog.description || ""}</div>
            </>
          )}
        </div>
        <div id="modal-actions">
          {dialog.kind === "suggest" ? (
            <>
              <button
                id="modal-yes"
                className="btn-danger"
                onClick={() => void sendSuggestReply(true)}
              >
                🎲 确定尝试
              </button>
              <button
                id="modal-no"
                className="btn-safe"
                onClick={() => void sendSuggestReply(false)}
              >
                ↩ 放弃
              </button>
            </>
          ) : (
            dialog.options.map((option) => (
              <button
                key={option.id}
                className={`${dangerousDecisionIds.has(option.id) ? "btn-danger" : "btn-safe"} decision-option`}
                onClick={() =>
                  void sendDecisionReply(
                    dialog.id,
                    option.id,
                    option.label || option.id,
                  )
                }
              >
                <strong>{option.label || option.id}</strong>
                {option.description && <span>{option.description}</span>}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
