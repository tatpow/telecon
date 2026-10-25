"use strict";

// Small, finite animations. Native dialog semantics and focus restoration stay intact.
const motion = (() => {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const closing = new WeakMap();
  const ownAnimations = new WeakMap();
  let visibleCards = new Set();
  function animate(element, frames, duration = 190, delay = 0) {
    ownAnimations.get(element)?.cancel();
    if (reduced.matches || !element.isConnected) return null;
    const animation = element.animate(frames, {
      duration, delay, fill: "backwards", easing: "cubic-bezier(.2, 0, 0, 1)"
    });
    ownAnimations.set(element, animation);
    return animation;
  }
  function enter(element, delay = 0) {
    return animate(element, [
      { opacity: 0, translate: "0 9px" },
      { opacity: 1, translate: "0 0" }
    ], 230, delay);
  }
  function open(dialog) {
    closing.delete(dialog);
    ownAnimations.get(dialog)?.cancel();
    if (!dialog.open) dialog.showModal();
    enter(dialog);
  }
  async function close(dialog) {
    if (!dialog.open || closing.has(dialog)) return;
    const token = {};
    closing.set(dialog, token);
    const animation = animate(dialog, [
      { opacity: 1, translate: "0 0", scale: 1 },
      { opacity: 0, translate: "0 6px", scale: .99 }
    ], 130);
    if (animation) {
      try { await animation.finished; } catch (_) { /* Interrupted by reopening. */ }
    }
    if (closing.get(dialog) === token) {
      closing.delete(dialog);
      dialog.close();
    }
  }
  function cards(lectures) {
    const next = new Set(lectures.map(item => item.id));
    let order = 0;
    for (const [index, card] of [...document.querySelectorAll(".lecture-card")].entries()) {
      if (!visibleCards.has(lectures[index].id)) enter(card, Math.min(order++, 4) * 24);
    }
    visibleCards = next;
  }
  reduced.addEventListener("change", event => {
    if (event.matches) for (const animation of document.getAnimations()) animation.finish();
  });
  return { animate, enter, open, close, cards, reduced };
})();
