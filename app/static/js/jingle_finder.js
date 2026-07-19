document.addEventListener("DOMContentLoaded", () => {
  const player = document.getElementById("player");
  const timeline = document.getElementById("timeline");
  const durationMs = timeline ? parseInt(timeline.dataset.durationMs, 10) || 0 : 0;
  const playhead = document.getElementById("playhead");

  document.querySelectorAll(".preview-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const start = parseFloat(btn.dataset.start);
      if (player && !Number.isNaN(start)) {
        player.currentTime = start;
        player.play();
      }
    });
  });

  document.querySelectorAll(".skip-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!player) return;
      const delta = parseFloat(btn.dataset.skip);
      const maxTime = player.duration || durationMs / 1000;
      player.currentTime = clampVal(player.currentTime + delta, 0, maxTime);
    });
  });

  const jumpToStartBtn = document.getElementById("jump-to-start");
  if (jumpToStartBtn) {
    jumpToStartBtn.addEventListener("click", () => {
      const startHidden = document.getElementById("sel-start-seconds");
      if (player && startHidden) {
        player.currentTime = parseFloat(startHidden.value) || 0;
        player.play();
      }
    });
  }

  if (!timeline || durationMs <= 0) return;

  const ruler = document.getElementById("timeline-ruler");
  buildTimelineRuler(ruler, durationMs);

  if (player) {
    player.addEventListener("timeupdate", () => {
      const ratio = clampVal(player.currentTime / (durationMs / 1000), 0, 1);
      if (playhead) playhead.style.left = `${ratio * 100}%`;
    });
  }

  // Generic drag-to-resize/move for one timeline segment element.
  function wireDraggableSegment(el, getPositions, onApply) {
    const left = el.querySelector(".handle-left");
    const right = el.querySelector(".handle-right");
    let drag = null;

    function apply(startMs, endMs) {
      el.style.left = `${(startMs / durationMs) * 100}%`;
      el.style.width = `${((endMs - startMs) / durationMs) * 100}%`;
      onApply(startMs, endMs);
    }

    function onPointerMove(e) {
      if (!drag) return;
      const rect = timeline.getBoundingClientRect();
      const msPerPixel = durationMs / rect.width;
      const deltaMs = (e.clientX - drag.startClientX) * msPerPixel;
      let newStart = drag.origStart;
      let newEnd = drag.origEnd;
      if (drag.mode === "start") {
        newStart = clampVal(drag.origStart + deltaMs, 0, drag.origEnd - 1000);
      } else if (drag.mode === "end") {
        newEnd = clampVal(drag.origEnd + deltaMs, drag.origStart + 1000, durationMs);
      } else {
        const length = drag.origEnd - drag.origStart;
        newStart = clampVal(drag.origStart + deltaMs, 0, durationMs - length);
        newEnd = newStart + length;
      }
      apply(newStart, newEnd);
    }
    function onPointerUp() {
      drag = null;
      document.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("pointerup", onPointerUp);
    }
    function startDrag(mode, clientX) {
      const { startMs, endMs } = getPositions();
      drag = { mode, origStart: startMs, origEnd: endMs, startClientX: clientX };
      document.addEventListener("pointermove", onPointerMove);
      document.addEventListener("pointerup", onPointerUp);
    }

    if (left) left.addEventListener("pointerdown", (e) => { e.stopPropagation(); e.preventDefault(); startDrag("start", e.clientX); });
    if (right) right.addEventListener("pointerdown", (e) => { e.stopPropagation(); e.preventDefault(); startDrag("end", e.clientX); });
    el.addEventListener("pointerdown", (e) => {
      if (e.target === left || e.target === right) return;
      e.preventDefault();
      startDrag("move", e.clientX);
    });
  }

  // ---- Auto mode: one draggable segment per candidate row ----
  document.querySelectorAll(".candidate-row").forEach((row) => {
    const index = row.dataset.index;
    const el = document.getElementById(`tl-cand-${index}`);
    if (!el) return;
    const startDisplay = row.querySelector(".cand-start-display");
    const endDisplay = row.querySelector(".cand-end-display");
    const startHidden = row.querySelector(".cand-start-seconds");
    const endHidden = row.querySelector(".cand-end-seconds");

    const onApply = (startMs, endMs) => {
      startDisplay.value = msToMMSS(startMs);
      endDisplay.value = msToMMSS(endMs);
      startHidden.value = (startMs / 1000).toFixed(2);
      endHidden.value = (endMs / 1000).toFixed(2);
    };
    const getPositions = () => ({
      startMs: Math.round(parseFloat(startHidden.value) * 1000),
      endMs: Math.round(parseFloat(endHidden.value) * 1000),
    });

    wireDraggableSegment(el, getPositions, onApply);

    const commit = () => {
      let startMs = mmssToMs(startDisplay.value);
      let endMs = mmssToMs(endDisplay.value);
      if (startMs === null) startMs = getPositions().startMs;
      if (endMs === null) endMs = getPositions().endMs;
      startMs = clampVal(startMs, 0, durationMs);
      endMs = clampVal(endMs, 0, durationMs);
      if (endMs - startMs < 1000) endMs = Math.min(durationMs, startMs + 1000);
      el.style.left = `${(startMs / durationMs) * 100}%`;
      el.style.width = `${((endMs - startMs) / durationMs) * 100}%`;
      onApply(startMs, endMs);
    };
    startDisplay.addEventListener("change", commit);
    endDisplay.addEventListener("change", commit);
  });

  // ---- Manual mode: single freeform selection ----
  const selectionEl = document.getElementById("tl-selection");
  const addSelectionBtn = document.getElementById("add-selection");
  if (selectionEl && addSelectionBtn) {
    const startDisplay = document.getElementById("sel-start-display");
    const endDisplay = document.getElementById("sel-end-display");
    const startHidden = document.getElementById("sel-start-seconds");
    const endHidden = document.getElementById("sel-end-seconds");
    const saveBtn = document.getElementById("save-selection-btn");

    const onApply = (startMs, endMs) => {
      startDisplay.value = msToMMSS(startMs);
      endDisplay.value = msToMMSS(endMs);
      startHidden.value = (startMs / 1000).toFixed(2);
      endHidden.value = (endMs / 1000).toFixed(2);
    };
    const getPositions = () => ({
      startMs: Math.round(parseFloat(startHidden.value) * 1000),
      endMs: Math.round(parseFloat(endHidden.value) * 1000),
    });

    wireDraggableSegment(selectionEl, getPositions, onApply);

    addSelectionBtn.addEventListener("click", () => {
      const center = player ? player.currentTime * 1000 : 0;
      const defaultLenMs = 5000;
      const startMs = clampVal(center, 0, Math.max(0, durationMs - defaultLenMs));
      const endMs = clampVal(startMs + defaultLenMs, 0, durationMs);
      selectionEl.style.display = "flex";
      selectionEl.style.left = `${(startMs / durationMs) * 100}%`;
      selectionEl.style.width = `${((endMs - startMs) / durationMs) * 100}%`;
      onApply(startMs, endMs);
      startDisplay.disabled = false;
      endDisplay.disabled = false;
      saveBtn.disabled = false;
    });

    const commit = () => {
      let startMs = mmssToMs(startDisplay.value);
      let endMs = mmssToMs(endDisplay.value);
      if (startMs === null) startMs = getPositions().startMs;
      if (endMs === null) endMs = getPositions().endMs;
      startMs = clampVal(startMs, 0, durationMs);
      endMs = clampVal(endMs, 0, durationMs);
      if (endMs - startMs < 1000) endMs = Math.min(durationMs, startMs + 1000);
      selectionEl.style.left = `${(startMs / durationMs) * 100}%`;
      selectionEl.style.width = `${((endMs - startMs) / durationMs) * 100}%`;
      onApply(startMs, endMs);
    };
    startDisplay.addEventListener("change", commit);
    endDisplay.addEventListener("change", commit);
  }

  // Click on empty timeline area seeks the player (both modes)
  timeline.addEventListener("click", (e) => {
    if (e.target !== timeline && e.target !== ruler) return;
    const rect = timeline.getBoundingClientRect();
    const ratio = clampVal((e.clientX - rect.left) / rect.width, 0, 1);
    if (player) player.currentTime = (ratio * durationMs) / 1000;
  });
});
