import {
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import type { ModuleInfo } from "../../api/modules";
import { orderModules } from "../../module-order";
import { ModuleCardInfo } from "./ModuleCardInfo";
import { useModuleCardDrag } from "./useModuleCardDrag";

export function useModuleOrdering<T extends ModuleInfo>(
  modules: T[],
  blocked: boolean,
  onEdit: () => void,
) {
  const [draft, setDraft] = useState<string[] | null>(null);
  const cards = draft ? orderModules(modules, draft) : modules;
  const dirty = cards.some((item, index) => item.id !== modules[index]?.id);
  const animations = useRef<Animation[]>([]);
  const keyboard = useRef<{
    tops: Map<Element, number>;
    handle: HTMLButtonElement;
  } | null>(null);
  const drag = useModuleCardDrag(({ id, before }) => {
    if (blocked) return;
    const ids = cards.filter((item) => item.id !== id).map((item) => item.id);
    const destination = before === null ? ids.length : ids.indexOf(before);
    if (destination < 0) return;
    ids.splice(destination, 0, id);
    setDraft(ids);
    onEdit();
  });

  function stopAnimations() {
    animations.current.forEach((animation) => animation.cancel());
    animations.current = [];
  }

  useLayoutEffect(() => {
    const pending = keyboard.current;
    if (!pending) return;
    keyboard.current = null;
    const shifts = Array.from(pending.tops, ([node, top]) => ({
      node,
      delta: top - node.getBoundingClientRect().top,
    }));
    if (!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      const style = getComputedStyle(pending.handle);
      const duration =
        parseFloat(style.getPropertyValue("--motion-feedback")) || 150;
      const easing =
        style.getPropertyValue("--motion-easing").trim() || "ease-out";
      animations.current = shifts.flatMap(({ node, delta }) =>
        node.isConnected && delta && node.animate
          ? [
              node.animate(
                [
                  { transform: `translateY(${delta}px)` },
                  { transform: "none" },
                ],
                { duration, easing },
              ),
            ]
          : [],
      );
    }
    if (pending.handle.isConnected) {
      pending.handle.focus({ preventScroll: true });
      pending.handle.scrollIntoView?.({ block: "nearest" });
    }
  }, [draft]);

  function moveWithKeys(
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) {
    if (event.key === "Escape" && drag.preview) {
      event.preventDefault();
      event.stopPropagation();
      drag.cancel();
      return;
    }
    if (
      blocked ||
      drag.preview ||
      (event.key !== "ArrowUp" && event.key !== "ArrowDown")
    )
      return;
    event.preventDefault();
    const target = index + (event.key === "ArrowUp" ? -1 : 1);
    if (target < 0 || target >= cards.length) return;
    const ids = cards.map((item) => item.id);
    const [id] = ids.splice(index, 1);
    ids.splice(target, 0, id);
    keyboard.current = {
      tops: new Map(
        Array.from(drag.listRef.current?.children ?? [], (node) => [
          node,
          node.getBoundingClientRect().top,
        ]),
      ),
      handle: event.currentTarget,
    };
    stopAnimations();
    setDraft(ids);
    onEdit();
  }

  return {
    cards,
    dirty,
    drag,
    moveWithKeys,
    stopAnimations,
    reset: () => setDraft(null),
  };
}

export function SortableModuleCards<T extends ModuleInfo>({
  ordering,
  helpId,
  blocked,
  local = false,
  details,
  action,
}: {
  ordering: ReturnType<typeof useModuleOrdering<T>>;
  helpId: string;
  blocked: boolean;
  local?: boolean;
  details?: (item: T) => ReactNode;
  action: (
    item: T,
  ) => { label: string; name: string; onClick: () => void } | null;
}) {
  const { cards, drag, moveWithKeys, stopAnimations } = ordering;
  const { preview } = drag;
  const lifted = preview && cards.find((item) => item.id === preview.id);
  const liftedAction = lifted && action(lifted);
  const liftRoot =
    preview && drag.listRef.current?.closest(".module-manager-overlay");

  function shiftCard(index: number) {
    const target = preview?.drop?.index;
    if (!preview || target === undefined) return;
    const source = preview.sourceIndex;
    if (index >= target && index < source) return "down";
    if (index > source && index <= target) return "up";
  }

  return (
    <>
      <ol
        ref={drag.listRef}
        className="module-library-cards"
        aria-label="模组排序"
        data-drop-end={preview?.drop?.before === null || undefined}
        data-sorting={Boolean(preview) || undefined}
        style={
          preview
            ? ({
                "--card-lift-height": `${preview.height}px`,
                "--card-gap-top": `${preview.gapTop}px`,
              } as CSSProperties)
            : undefined
        }
      >
        {cards.map((item, index) => {
          const control = action(item);
          return (
            <li
              className="module-library-card"
              key={item.id}
              data-module-id={item.id}
              data-dragging={preview?.id === item.id || undefined}
              data-card-shift={shiftCard(index)}
            >
              <button
                type="button"
                className="module-card-grip"
                title="拖动排序，也可使用上下方向键"
                aria-label={`调整${item.title}顺序，第 ${index + 1} 项`}
                aria-describedby={helpId}
                disabled={blocked || cards.length < 2}
                data-dialog-escape={preview?.id === item.id || undefined}
                onPointerDown={(event) => {
                  stopAnimations();
                  drag.begin(event, item.id);
                }}
                onPointerMove={drag.move}
                onPointerUp={drag.finish}
                onPointerCancel={drag.interrupt}
                onLostPointerCapture={drag.interrupt}
                onKeyDown={(event) => moveWithKeys(event, index)}
              >
                <span aria-hidden="true" />
              </button>
              <ModuleCardInfo item={item} local={local}>
                {details?.(item)}
              </ModuleCardInfo>
              {control && (
                <button
                  type="button"
                  className="btn-ghost module-library-remove"
                  disabled={blocked || Boolean(preview)}
                  aria-label={control.name}
                  onClick={control.onClick}
                >
                  {control.label}
                </button>
              )}
            </li>
          );
        })}
      </ol>
      {preview &&
        lifted &&
        liftRoot &&
        createPortal(
          <div
            className="module-library-card cloud-module-lift"
            aria-hidden="true"
            style={{
              width: preview.width,
              height: preview.height,
              translate: `${preview.left}px ${preview.top}px`,
            }}
          >
            <div className="module-card-grip">
              <span />
            </div>
            <ModuleCardInfo item={lifted} local={local}>
              {details?.(lifted)}
            </ModuleCardInfo>
            {liftedAction && (
              <span className="btn-ghost module-library-remove">
                {liftedAction.label}
              </span>
            )}
          </div>,
          liftRoot,
        )}
    </>
  );
}
