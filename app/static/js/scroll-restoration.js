/*
 * Restore page and nested scroll positions after navigation and POST/Redirect/GET.
 * Uses sessionStorage and never interferes with form submission or browser navigation.
 */
(() => {
  "use strict";

  const PREFIX = "pewpewadpod:scroll:v2:";
  const MAX_AGE_MS = 24 * 60 * 60 * 1000;
  const pageKey = PREFIX + window.location.pathname + window.location.search;
  let restoreStarted = false;
  let userInteracted = false;
  let saveTimer = null;

  function storage() {
    try {
      return window.sessionStorage;
    } catch (_) {
      return null;
    }
  }

  function selectorFor(element) {
    if (element.id && /^[A-Za-z][\w:.-]*$/.test(element.id)) {
      return `#${element.id}`;
    }

    const parts = [];
    let current = element;
    while (current && current !== document.body && current.nodeType === 1) {
      let part = current.tagName.toLowerCase();
      const classes = Array.from(current.classList || [])
        .filter(name => /^[A-Za-z0-9_-]+$/.test(name))
        .slice(0, 3);
      if (classes.length) part += classes.map(name => `.${name}`).join("");

      const parent = current.parentElement;
      if (parent) {
        const sameTag = Array.from(parent.children).filter(
          sibling => sibling.tagName === current.tagName
        );
        if (sameTag.length > 1) {
          part += `:nth-of-type(${sameTag.indexOf(current) + 1})`;
        }
      }
      parts.unshift(part);
      current = parent;
    }
    return parts.length ? `body > ${parts.join(" > ")}` : null;
  }

  function scrollableElements() {
    return Array.from(document.querySelectorAll("body *")).filter(element => {
      const style = window.getComputedStyle(element);
      const xScrollable = /^(auto|scroll)$/.test(style.overflowX) &&
        element.scrollWidth > element.clientWidth + 2;
      const yScrollable = /^(auto|scroll)$/.test(style.overflowY) &&
        element.scrollHeight > element.clientHeight + 2;
      return xScrollable || yScrollable;
    });
  }

  function saveNow() {
    const store = storage();
    if (!store) return;

    const nested = [];
    for (const element of scrollableElements()) {
      const selector = selectorFor(element);
      if (selector) {
        nested.push({ selector, left: element.scrollLeft, top: element.scrollTop });
      }
    }

    const state = {
      savedAt: Date.now(),
      left: window.scrollX || 0,
      top: window.scrollY || 0,
      nested
    };

    try {
      store.setItem(pageKey, JSON.stringify(state));
    } catch (_) {
      // Storage can be disabled or full; scrolling/navigation must still work.
    }
  }

  function scheduleSave() {
    if (saveTimer !== null) window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(saveNow, 100);
  }

  function loadState() {
    const store = storage();
    if (!store) return null;
    try {
      const raw = store.getItem(pageKey);
      if (!raw) return null;
      const state = JSON.parse(raw);
      if (!state || !Number.isFinite(state.savedAt) ||
          Date.now() - state.savedAt > MAX_AGE_MS) {
        store.removeItem(pageKey);
        return null;
      }
      return state;
    } catch (_) {
      return null;
    }
  }

  function restoreNow() {
    if (restoreStarted || userInteracted) return;
    const state = loadState();
    if (!state) return;
    restoreStarted = true;

    // Use positional scrollTo (rather than smooth behavior) to avoid animating
    // the page away from the user's intended position.
    window.scrollTo(Number(state.left) || 0, Number(state.top) || 0);

    if (Array.isArray(state.nested)) {
      for (const saved of state.nested) {
        if (!saved || typeof saved.selector !== "string") continue;
        try {
          const element = document.querySelector(saved.selector);
          if (!element) continue;
          element.scrollLeft = Number(saved.left) || 0;
          element.scrollTop = Number(saved.top) || 0;
        } catch (_) {
          // Ignore selectors for elements removed by a newer page version.
        }
      }
    }
  }

  // Continuously persist positions, including inner scroll containers such as
  // the playlist list/table. Capture is needed because element scroll does not bubble.
  window.addEventListener("scroll", scheduleSave, { passive: true });
  document.addEventListener("scroll", scheduleSave, { passive: true, capture: true });
  window.addEventListener("pagehide", saveNow);

  // Save before navigating to another same-origin page.
  document.addEventListener("click", event => {
    const target = event.target;
    const link = target && target.closest ? target.closest("a[href]") : null;
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey ||
        event.ctrlKey || event.shiftKey || event.altKey || link.target === "_blank" ||
        link.hasAttribute("download")) return;

    try {
      const destination = new URL(link.href, window.location.href);
      if (destination.origin === window.location.origin) saveNow();
    } catch (_) {
      // Ignore malformed/non-navigation URLs.
    }
  }, true);

  // Covers POST actions followed by RedirectResponse (e.g. heard-elsewhere,
  // move-up/down, bulk actions, delete/reanalyse).
  document.addEventListener("submit", saveNow, true);

  // Do not fight the user if they start scrolling/using the page before restore.
  ["wheel", "touchstart", "pointerdown", "keydown"].forEach(type => {
    window.addEventListener(type, () => { userInteracted = true; }, {
      passive: true,
      once: true
    });
  });

  // Normal navigation and POST redirects do not use BFCache; for BFCache the
  // browser already restores the complete live page, including nested scrollers.
  window.addEventListener("pageshow", event => {
    if (event.persisted) return;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => window.setTimeout(restoreNow, 50));
    });
  });
  window.addEventListener("load", () => {
    window.setTimeout(restoreNow, 120);
  }, { once: true });
})();
