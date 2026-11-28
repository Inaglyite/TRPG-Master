import { useEffect, useRef, useState, type PointerEvent } from "react";

type Drop = {
  id: string;
  before: string | null;
  index: number;
};
type Preview = {
  id: string;
  drop: Drop | null;
  sourceIndex: number;
  gapTop: number;
  left: number;
  top: number;
  width: number;
  height: number;
};
type Gesture = {
  id: string;
  pointerId: number;
  handle: HTMLButtonElement;
  card: HTMLElement;
  x: number;
  y: number;
  startX: number;
  startY: number;
  cardRect: DOMRect;
  list: HTMLOListElement;
  scroller: HTMLElement;
  frame: number;
  moved: boolean;
};

function canDrop(gesture: Gesture, bounds: DOMRect) {
  return (
    gesture.x >= bounds.left &&
    gesture.x <= bounds.right &&
    gesture.y >= bounds.top &&
    gesture.y <= bounds.bottom
  );
}

function readPreview(gesture: Gesture, inBounds: boolean): Preview {
  const { id, list, cardRect, x, y, startX, startY } = gesture;
  const top = cardRect.top + y - startY;
  const cards = Array.from(list.children) as HTMLElement[];
  const sourceIndex = cards.indexOf(gesture.card);
  let gapTop = gesture.card.offsetTop;
  let drop: Drop | null = null;
  if (inBounds && sourceIndex !== -1) {
    // Measure the original slots. Measuring animated cards would move the
    // threshold while the pointer is held still and can make the list flicker.
    const center = top + cardRect.height / 2 - list.getBoundingClientRect().top;
    const otherCards = cards.filter((card) => card !== gesture.card);
    const nextIndex = otherCards.findIndex(
      (card) => center < card.offsetTop + card.offsetHeight / 2,
    );
    const index = nextIndex === -1 ? otherCards.length : nextIndex;
    const slot = cards[index];
    gapTop = slot.offsetTop;
    if (index > sourceIndex) gapTop += slot.offsetHeight - cardRect.height;
    drop = { id, before: otherCards[index]?.dataset.moduleId ?? null, index };
  }
  return {
    id,
    drop,
    sourceIndex,
    gapTop,
    left: cardRect.left + x - startX,
    top,
    width: cardRect.width,
    height: cardRect.height,
  };
}

/** Keep pointer capture on the original card while its visual copy follows the pointer. */
export function useModuleCardDrag(onDrop: (drop: Drop) => void) {
  const listRef = useRef<HTMLOListElement>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);

  function cancel() {
    const gesture = gestureRef.current;
    gestureRef.current = null;
    if (gesture) {
      cancelAnimationFrame(gesture.frame);
      if (gesture.handle.hasPointerCapture(gesture.pointerId)) {
        gesture.handle.releasePointerCapture(gesture.pointerId);
      }
    }
    setPreview(null);
  }

  useEffect(
    () => () => {
      const gesture = gestureRef.current;
      if (gesture) cancelAnimationFrame(gesture.frame);
      gestureRef.current = null;
    },
    [],
  );

  function begin(event: PointerEvent<HTMLButtonElement>, id: string) {
    const list = listRef.current;
    const scroller = list?.closest<HTMLElement>("[data-dialog-scroll]");
    const card = event.currentTarget.closest<HTMLElement>("[data-module-id]");
    if (!event.isPrimary || event.button !== 0 || !list || !scroller || !card)
      return;
    cancel();
    event.currentTarget.setPointerCapture(event.pointerId);
    const gesture: Gesture = {
      id,
      pointerId: event.pointerId,
      handle: event.currentTarget,
      card,
      x: event.clientX,
      y: event.clientY,
      startX: event.clientX,
      startY: event.clientY,
      cardRect: card.getBoundingClientRect(),
      list,
      scroller,
      frame: 0,
      moved: false,
    };
    gestureRef.current = gesture;

    function update() {
      if (gestureRef.current !== gesture) return;
      if (gesture.moved) {
        const bounds = gesture.scroller.getBoundingClientRect();
        const inBounds = canDrop(gesture, bounds);
        if (inBounds) {
          const edge = Math.min(36, bounds.height / 4);
          if (gesture.y < bounds.top + edge) gesture.scroller.scrollTop -= 10;
          if (gesture.y > bounds.bottom - edge)
            gesture.scroller.scrollTop += 10;
        }
        const next = readPreview(gesture, inBounds);
        setPreview((previous) =>
          previous?.id === next.id &&
          previous.left === next.left &&
          previous.top === next.top &&
          previous.gapTop === next.gapTop &&
          previous.drop?.before === next.drop?.before
            ? previous
            : next,
        );
      }
      gesture.frame = requestAnimationFrame(update);
    }

    gesture.frame = requestAnimationFrame(update);
  }

  function move(event: PointerEvent<HTMLButtonElement>) {
    const gesture = gestureRef.current;
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    gesture.x = event.clientX;
    gesture.y = event.clientY;
    gesture.moved ||=
      Math.hypot(gesture.x - gesture.startX, gesture.y - gesture.startY) > 5;
  }

  function finish(event: PointerEvent<HTMLButtonElement>) {
    const gesture = gestureRef.current;
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    move(event);
    const target = gesture.moved
      ? readPreview(
          gesture,
          canDrop(gesture, gesture.scroller.getBoundingClientRect()),
        ).drop
      : null;
    cancel();
    if (target) onDrop(target);
  }

  function interrupt(event: PointerEvent<HTMLButtonElement>) {
    if (event.pointerId === gestureRef.current?.pointerId) cancel();
  }

  return { listRef, preview, begin, move, finish, cancel, interrupt };
}
