import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  cancelDice3D,
  isDice3DBusy,
  isDice3DEligible,
  rollDice3D,
} from "../../dice3d/controller";
import { renderMarkdown } from "../../markdown";
import { NARRATION_LONG_PRESS_MS } from "../../narration-speed";
import {
  invokeTurnBranch,
  invokeTurnRewrite,
  setNarrationBoost,
} from "../../renderer";
import { useMessageStore, type ChatMessage } from "../../state/message-store";
import type { NarrativeSegment } from "../../state/message-store";
import { useAppStore } from "../../state/app-store";
import {
  useOnlineStore,
  useTimelineCapabilities,
} from "../../state/online-store";
import { AvatarDisc } from "./AvatarDisc";

/**
 * 流式叙述区域的长按加速：按住达到阈值后约 3 倍速播放，
 * 松开、移出、指针取消、窗口失焦或流结束时立即恢复所选档位。
 * 普通单击不产生任何播放行为。
 */
function useNarrationLongPress(enabled: boolean) {
  const [boosting, setBoosting] = useState(false);
  const pressTimer = useRef<number | null>(null);
  const boostActive = useRef(false);

  const endBoost = useCallback(() => {
    if (pressTimer.current !== null) {
      window.clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
    if (boostActive.current) {
      boostActive.current = false;
      setNarrationBoost(false);
    }
    setBoosting(false);
  }, []);

  useEffect(() => {
    if (!enabled) {
      endBoost();
      return;
    }
    const onWindowBlur = () => endBoost();
    window.addEventListener("blur", onWindowBlur);
    return () => {
      window.removeEventListener("blur", onWindowBlur);
      endBoost();
    };
  }, [enabled, endBoost]);

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      if (!enabled || boostActive.current || pressTimer.current !== null)
        return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      if ((event.target as HTMLElement).closest("a, button")) return;
      pressTimer.current = window.setTimeout(() => {
        pressTimer.current = null;
        boostActive.current = true;
        setNarrationBoost(true);
        setBoosting(true);
      }, NARRATION_LONG_PRESS_MS);
    },
    [enabled],
  );

  return { boosting, onPointerDown, endBoost };
}

function LoadingMessage({ label }: { label: string }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.round((Date.now() - started) / 1000)),
      5000,
    );
    return () => window.clearInterval(timer);
  }, []);
  return (
    <>
      <div className="typing-dots">
        <span />
        <span />
        <span />
      </div>
      <span className="loading-label">
        {elapsed >= 8 ? `${label}（已等待 ${elapsed} 秒）` : label}
      </span>
    </>
  );
}

function DiceMessage({ message }: { message: ChatMessage }) {
  const dice = message.dice || [];
  const [settled, setSettled] = useState(dice.length === 0);
  useEffect(() => {
    if (
      !dice.length ||
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ) {
      setSettled(true);
      return;
    }

    let cancelled = false;
    let used3D = false;
    let timeout = 0;

    if (isDice3DEligible(dice) && !isDice3DBusy()) {
      // 3D 物理骰表演（覆盖层），定格/超时/跳过后落定
      used3D = true;
      rollDice3D(dice)
        .catch(() => undefined)
        .finally(() => {
          if (!cancelled) setSettled(true);
        });
    } else {
      // 3D 不可用时的回退：短暂展示“翻滚”标题后直接落定结果
      timeout = window.setTimeout(() => {
        if (!cancelled) setSettled(true);
      }, 600);
    }
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
      if (used3D) cancelDice3D();
    };
  }, [dice]);

  if (!dice.length)
    return (
      <div
        id={message.id}
        className="msg dice"
        data-turn-id={message.turnId}
        dangerouslySetInnerHTML={{ __html: renderMarkdown(message.text) }}
      />
    );
  return (
    <div
      id={message.id}
      className={`msg dice ${settled ? "settled" : "rolling"}`}
      data-turn-id={message.turnId}
    >
      <div className="dice-title">
        {settled ? "命运之骰落定" : "命运之骰翻滚"}
      </div>
      <div
        className={`dice-result${settled ? "" : " hidden"}`}
        aria-live="polite"
      >
        {message.text}
      </div>
    </div>
  );
}

function NarrationUnit({ text }: { text: string }) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return (
    <div className="chat-row chat-row-keeper">
      <AvatarDisc name="守秘人" family="keeper" />
      <div className="chat-speaker-column">
        <div className="chat-speaker-name keeper-name">
          守秘人<span>THE KEEPER</span>
        </div>
        <div
          className="chat-bubble keeper-bubble"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </div>
  );
}

function SpeechUnit({ segment }: { segment: NarrativeSegment }) {
  const html = useMemo(() => renderMarkdown(segment.text), [segment.text]);
  const name = segment.speaker?.name || "人物";
  return (
    <div className="chat-row chat-row-npc speech-unit">
      <AvatarDisc name={name} avatar={segment.speaker?.avatar} family="npc" />
      <div className="chat-speaker-column speech-body">
        <div className="chat-speaker-name speech-name">{name}</div>
        <div
          className="chat-bubble npc-bubble speech-text"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </div>
  );
}

function Message({
  message,
  actionReset,
  sameTurn,
}: {
  message: ChatMessage;
  actionReset: number;
  sameTurn: boolean;
}) {
  const [actionsDisabled, setActionsDisabled] = useState(false);
  useEffect(() => setActionsDisabled(false), [actionReset]);
  const html = useMemo(() => renderMarkdown(message.text), [message.text]);
  const character = useAppStore((state) => state.character);
  const mode = useAppStore((state) => state.mode);
  // 联机：时间线分支仅云端单人房主可用（多人房间服务端结构性拒绝，
  // 按钮直接隐藏）；回合改写仅房主可用（服务端仍按 Session 再校验），
  // 单机行为不变。
  const isRoomOwner = useOnlineStore((state) => {
    const uid = state.user?.id;
    return (
      uid != null &&
      state.members.some(
        (member) => member.user_id === uid && member.role === "owner",
      )
    );
  });
  const rewriteVisible = mode !== "online" || isRoomOwner;
  const timelineCaps = useTimelineCapabilities();
  const branchVisible = mode !== "online" || timelineCaps.canCreateBranch;
  // 仅流式呈现中的守秘人叙述区域响应长按加速；单击不做任何播放操作。
  const longPress = useNarrationLongPress(
    message.kind === "gm" && Boolean(message.streaming),
  );
  const playerName =
    message.speaker?.name ||
    (mode !== "online" ? character?.name : undefined) ||
    "调查员";
  const playerAvatar =
    message.speaker?.avatar ||
    (mode !== "online" ? character?.avatar : undefined);
  if (message.hidden) return null;
  if (message.kind === "dice") return <DiceMessage message={message} />;
  const className = [
    "msg",
    message.kind,
    message.streaming ? "streaming-cursor" : "",
    longPress.boosting ? "narration-boosting" : "",
    message.rewriteTarget ? "rewrite-target" : "",
    sameTurn ? "same-turn" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const hasSegments =
    message.kind === "gm" && Boolean(message.segments?.length);

  return (
    <div
      id={message.id}
      className={className}
      data-turn-id={message.turnId}
      onPointerDown={longPress.onPointerDown}
      onPointerUp={longPress.endBoost}
      onPointerLeave={longPress.endBoost}
      onPointerCancel={longPress.endBoost}
    >
      {message.kind === "gm" && !hasSegments && (
        <div className="msg-attribution">
          守秘人<span>THE KEEPER</span>
        </div>
      )}
      {message.kind === "player" && (
        <div className="msg-attribution msg-attribution-investigator">
          <span className="msg-attribution-name">{playerName}</span>
          <AvatarDisc
            name={playerName}
            avatar={playerAvatar}
            family="investigator"
          />
        </div>
      )}
      {message.kind === "loading" ? (
        <LoadingMessage label={message.text} />
      ) : message.kind === "roll-pending" ? (
        <>
          <div className="dice-title">判定中</div>
          <div className="typing-dots">
            <span />
            <span />
            <span />
          </div>
          <div className="dice-result">{message.text}</div>
        </>
      ) : hasSegments ? (
        <div className="chat-event-list">
          {message.segments!.map((segment, index) =>
            segment.kind === "speech" ? (
              <SpeechUnit key={segment.eventId || index} segment={segment} />
            ) : (
              <NarrationUnit
                key={segment.eventId || index}
                text={segment.text}
              />
            ),
          )}
        </div>
      ) : (
        <div dangerouslySetInnerHTML={{ __html: html }} />
      )}
      {((rewriteVisible && message.canRewrite) ||
        (branchVisible && message.canBranch)) &&
        message.turnId && (
          <div className="turn-message-actions">
            {rewriteVisible && message.canRewrite && (
              <button
                type="button"
                className="turn-rewrite-button"
                disabled={actionsDisabled}
                title="重新叙述（不重新判定）"
                aria-label="重新叙述本回合，不改变判定结果"
                onClick={() => {
                  setActionsDisabled(true);
                  invokeTurnRewrite(message.turnId!);
                }}
              >
                ↻
              </button>
            )}
            {branchVisible && message.canBranch && (
              <button
                type="button"
                className="turn-branch-button"
                disabled={actionsDisabled}
                title="从本次行动前创建时间线分支"
                aria-label="回到本次行动前并创建独立时间线分支"
                onClick={() => {
                  setActionsDisabled(true);
                  invokeTurnBranch(message.turnId!);
                }}
              >
                ⑂
              </button>
            )}
          </div>
        )}
    </div>
  );
}

export function MessageList() {
  const messages = useMessageStore((state) => state.messages);
  const scrollRequest = useMessageStore((state) => state.scrollRequest);
  const forceScrollRequest = useMessageStore(
    (state) => state.forceScrollRequest,
  );
  const actionReset = useMessageStore((state) => state.actionReset);
  const container = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const lastForce = useRef(forceScrollRequest);

  useLayoutEffect(() => {
    const element = container.current;
    if (!element) return;
    const forced = forceScrollRequest !== lastForce.current;
    lastForce.current = forceScrollRequest;
    if (forced || pinned.current) element.scrollTop = element.scrollHeight;
  }, [messages, scrollRequest, forceScrollRequest]);

  return (
    <div
      id="messages"
      ref={container}
      onScroll={(event) => {
        const element = event.currentTarget;
        pinned.current =
          element.scrollHeight - element.scrollTop - element.clientHeight < 8;
      }}
    >
      {messages.map((message, index) => (
        <Message
          key={message.id}
          message={message}
          actionReset={actionReset}
          sameTurn={
            index > 0 &&
            Boolean(message.turnId) &&
            message.turnId === messages[index - 1].turnId
          }
        />
      ))}
    </div>
  );
}
