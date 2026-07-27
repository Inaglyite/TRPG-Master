import { useEffect, useRef, useState } from "react";

import { sendAction, sendDecisionReply, sendSuggestReply } from "../../options";
import { confirmEnding } from "../../panels";
import { useAppStore, type EndingProposal } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";

export function GameControls() {
  const appEnabled = useAppStore((state) => state.inputEnabled);
  const appPlaceholder = useAppStore((state) => state.inputPlaceholder);
  const choices = useAppStore((state) => state.choices);
  const ending = useAppStore((state) => state.ending);
  const mode = useAppStore((state) => state.mode);
  const roomStatus = useOnlineStore((state) => state.roomStatus);
  const roomConnection = useOnlineStore((state) => state.roomConnection);
  const currentActorUserId = useOnlineStore(
    (state) => state.currentActorUserId,
  );
  const userId = useOnlineStore((state) => state.user?.id);
  const members = useOnlineStore((state) => state.members);
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // 多人进行中：只有当前行动者可以提交；其他人输入与选项均禁用并显示等待。
  const roomPlaying = mode === "online" && roomStatus === "playing";
  const myRole = members.find((member) => member.user_id === userId)?.role;
  const myTurn =
    mode !== "online" ||
    (roomPlaying &&
      roomConnection === "connected" &&
      userId != null &&
      currentActorUserId === userId &&
      (myRole === "owner" || myRole === "player"));
  const enabled = appEnabled && myTurn;
  // 结案（settle_case）为房主专属操作；普通成员只看到继续探索。
  // selector 订阅成员变化，房主移交后按钮即时更新。
  const isOwner = useOnlineStore((state) => {
    const uid = state.user?.id;
    return (
      uid != null &&
      state.members.some(
        (member) => member.user_id === uid && member.role === "owner",
      )
    );
  });
  const settleVisible = mode !== "online" || isOwner;
  const actorName = members.find(
    (member) => member.user_id === currentActorUserId,
  )?.username;
  const placeholder =
    roomPlaying && !myTurn
      ? `等待 ${actorName ?? "行动者"} 行动……`
      : appPlaceholder;

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
            {settleVisible && (
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
            )}
            <button
              id="btn-end-continue"
              className="opt-btn free end-continue"
              disabled={!enabled}
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
              disabled={!enabled}
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
