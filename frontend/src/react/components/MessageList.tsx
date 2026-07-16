import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { renderMarkdown } from "../../markdown";
import { invokeTurnBranch, invokeTurnRewrite } from "../../renderer";
import {
  useMessageStore,
  type ChatMessage,
  type VisualDie,
} from "../../state/message-store";

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

function formatDie(die: VisualDie, value: number) {
  return die.formatter === "tens"
    ? value === 0
      ? "00"
      : String(value * 10)
    : String(value);
}

function randomValue(die: VisualDie) {
  return Math.floor(Math.random() * (die.max - die.min + 1)) + die.min;
}

function DiceMessage({ message }: { message: ChatMessage }) {
  const dice = message.dice || [];
  const [values, setValues] = useState(() => dice.map(randomValue));
  const [settled, setSettled] = useState(dice.length === 0);
  useEffect(() => {
    if (
      !dice.length ||
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ) {
      setValues(dice.map((die) => die.final));
      setSettled(true);
      return;
    }
    const interval = window.setInterval(
      () => setValues(dice.map(randomValue)),
      48,
    );
    const timeout = window.setTimeout(
      () => {
        window.clearInterval(interval);
        setValues(dice.map((die) => die.final));
        setSettled(true);
      },
      560 + dice.length * 80,
    );
    return () => {
      window.clearInterval(interval);
      window.clearTimeout(timeout);
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
      <div className="dice-title">命运之骰翻滚</div>
      <div className="dice-stage">
        {dice.map((die, index) => (
          <div className="dice-wrap" key={`${die.label}-${index}`}>
            <div
              className={`dice-face${settled ? " locked" : ""}`}
              data-sides={die.max}
            >
              <span>{formatDie(die, values[index] ?? die.final)}</span>
            </div>
            <div className="dice-face-label">{die.label}</div>
          </div>
        ))}
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

function Message({
  message,
  actionReset,
}: {
  message: ChatMessage;
  actionReset: number;
}) {
  const [actionsDisabled, setActionsDisabled] = useState(false);
  useEffect(() => setActionsDisabled(false), [actionReset]);
  const html = useMemo(() => renderMarkdown(message.text), [message.text]);
  if (message.hidden) return null;
  if (message.kind === "dice") return <DiceMessage message={message} />;
  const className = [
    "msg",
    message.kind,
    message.streaming ? "streaming-cursor" : "",
    message.rewriteTarget ? "rewrite-target" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div id={message.id} className={className} data-turn-id={message.turnId}>
      {message.kind === "loading" ? (
        <LoadingMessage label={message.text} />
      ) : message.kind === "roll-pending" ? (
        <>
          <div className="dice-title">判定中</div>
          <div className="dice-stage">
            <div className="dice-wrap">
              <div className="dice-face">
                <span>?</span>
              </div>
              <div className="dice-face-label">等待结果</div>
            </div>
          </div>
          <div className="dice-result">{message.text}</div>
        </>
      ) : (
        <div dangerouslySetInnerHTML={{ __html: html }} />
      )}
      {(message.canRewrite || message.canBranch) && message.turnId && (
        <div className="turn-message-actions">
          {message.canRewrite && (
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
          {message.canBranch && (
            <button
              type="button"
              className="turn-branch-button"
              disabled={actionsDisabled}
              title="从此回合创建时间线分支"
              aria-label="从此回合创建独立时间线分支"
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
      {messages.map((message) => (
        <Message key={message.id} message={message} actionReset={actionReset} />
      ))}
    </div>
  );
}
