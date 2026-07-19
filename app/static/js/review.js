document.addEventListener("DOMContentLoaded", () => {
  const player = document.getElementById("player");
  const timeline = document.getElementById("timeline");
  const durationMs = timeline ? parseInt(timeline.dataset.durationMs, 10) || 0 : 0;
  const playhead = document.getElementById("playhead");

  // ---- Preview buttons ----
  document.querySelectorAll(".preview-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const start = parseFloat(btn.dataset.start);
      if (player && !Number.isNaN(start)) {
        player.currentTime = start;
        player.play();
      }
    });
  });

  // ---- Manual add-segment rows ----
  const manualRows = document.getElementById("manual-rows");
  const addBtn = document.getElementById("add-manual-row");
  if (addBtn) {
    addBtn.addEventListener("click", () => {
      const row = document.createElement("div");
      row.className = "flex manual-row";
      row.innerHTML = `
        <label>Start <input type="text" class="mmss-input manual-start-display" placeholder="M:SS"></label>
        <input type="hidden" name="manual_start" class="manual-start-seconds">
        <label>Ende <input type="text" class="mmss-input manual-end-display" placeholder="M:SS"></label>
        <input type="hidden" name="manual_end" class="manual-end-seconds">
        <button type="button" class="secondary remove-row">Entfernen</button>
      `;
      row.querySelector(".remove-row").addEventListener("click", () => row.remove());
      manualRows.appendChild(row);
    });
  }

  // ---- Timeline setup ----
  if (!timeline || durationMs <= 0) return;

  const ruler = document.getElementById("timeline-ruler");
  buildTimelineRuler(ruler, durationMs);

  function segmentRow(segmentId) {
    return document.querySelector(`.segment-row[data-segment-id="${segmentId}"]`);
  }

  function applyPositions(segmentId, startMs, endMs) {
    const el = document.getElementById(`tl-seg-${segmentId}`);
    if (el) {
      el.style.left = `${(startMs / durationMs) * 100}%`;
      el.style.width = `${((endMs - startMs) / durationMs) * 100}%`;
    }
    const row = segmentRow(segmentId);
    if (!row) return;
    const startDisplay = row.querySelector(".seg-start-display");
    const endDisplay = row.querySelector(".seg-end-display");
    const startHidden = row.querySelector(".seg-start-seconds");
    const endHidden = row.querySelector(".seg-end-seconds");
    startDisplay.value = msToMMSS(startMs);
    endDisplay.value = msToMMSS(endMs);
    startHidden.value = (startMs / 1000).toFixed(1);
    endHidden.value = (endMs / 1000).toFixed(1);
  }

  function readPositions(segmentId) {
    const row = segmentRow(segmentId);
    const startMs = Math.round(parseFloat(row.querySelector(".seg-start-seconds").value) * 1000);
    const endMs = Math.round(parseFloat(row.querySelector(".seg-end-seconds").value) * 1000);
    return { startMs, endMs };
  }

  // Sync visible mm:ss inputs -> hidden seconds inputs + timeline, on manual edit.
  document.querySelectorAll(".segment-row").forEach((row) => {
    const segmentId = row.dataset.segmentId;
    const startDisplay = row.querySelector(".seg-start-display");
    const endDisplay = row.querySelector(".seg-end-display");

    const commit = () => {
      let startMs = mmssToMs(startDisplay.value);
      let endMs = mmssToMs(endDisplay.value);
      if (startMs === null) startMs = readPositions(segmentId).startMs;
      if (endMs === null) endMs = readPositions(segmentId).endMs;
      startMs = Math.max(0, Math.min(startMs, durationMs));
      endMs = Math.max(0, Math.min(endMs, durationMs));
      if (endMs - startMs < 1000) {
        endMs = Math.min(durationMs, startMs + 1000);
      }
      applyPositions(segmentId, startMs, endMs);
    };
    startDisplay.addEventListener("change", commit);
    endDisplay.addEventListener("change", commit);
  });

  // ---- Drag to resize / move ----
  let drag = null; // { segmentId, mode: 'start'|'end'|'move', origStart, origEnd, startClientX }

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
    } else if (drag.mode === "move") {
      const length = drag.origEnd - drag.origStart;
      newStart = clampVal(drag.origStart + deltaMs, 0, durationMs - length);
      newEnd = newStart + length;
    }
    applyPositions(drag.segmentId, newStart, newEnd);
  }

  function onPointerUp() {
    drag = null;
    document.removeEventListener("pointermove", onPointerMove);
    document.removeEventListener("pointerup", onPointerUp);
  }

  function startDrag(segmentId, mode, clientX) {
    const { startMs, endMs } = readPositions(segmentId);
    drag = { segmentId, mode, origStart: startMs, origEnd: endMs, startClientX: clientX };
    document.addEventListener("pointermove", onPointerMove);
    document.addEventListener("pointerup", onPointerUp);
  }

  timeline.querySelectorAll(".timeline-segment").forEach((el) => {
    const segmentId = el.dataset.segmentId;
    const left = el.querySelector(".handle-left");
    const right = el.querySelector(".handle-right");

    left.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      e.preventDefault();
      startDrag(segmentId, "start", e.clientX);
    });
    right.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      e.preventDefault();
      startDrag(segmentId, "end", e.clientX);
    });
    el.addEventListener("pointerdown", (e) => {
      if (e.target === left || e.target === right) return;
      e.preventDefault();
      startDrag(segmentId, "move", e.clientX);
    });
  });

  // Checkbox mutes/highlights the timeline block
  document.querySelectorAll(".seg-accept").forEach((cb) => {
    const row = cb.closest(".segment-row");
    const segmentId = row.dataset.segmentId;
    const el = document.getElementById(`tl-seg-${segmentId}`);
    const sync = () => el && el.classList.toggle("unchecked", !cb.checked);
    sync();
    cb.addEventListener("change", sync);
  });

  // Click on empty timeline area seeks the player
  timeline.addEventListener("click", (e) => {
    if (e.target !== timeline && e.target !== ruler) return;
    const rect = timeline.getBoundingClientRect();
    const ratio = clampVal((e.clientX - rect.left) / rect.width, 0, 1);
    if (player) player.currentTime = (ratio * durationMs) / 1000;
  });

  // Live playhead
  if (player) {
    player.addEventListener("timeupdate", () => {
      const ratio = clampVal(player.currentTime / (durationMs / 1000), 0, 1);
      playhead.style.left = `${ratio * 100}%`;
    });
  }

  // Final safety net: sync any unblurred mm:ss edits right before submit.
  const form = document.getElementById("review-form");
  if (form) {
    form.addEventListener("submit", () => {
      document.querySelectorAll(".segment-row").forEach((row) => {
        row.querySelector(".seg-start-display").dispatchEvent(new Event("change"));
        row.querySelector(".seg-end-display").dispatchEvent(new Event("change"));
      });
      document.querySelectorAll(".manual-row").forEach((row) => {
        const startDisplay = row.querySelector(".manual-start-display");
        const endDisplay = row.querySelector(".manual-end-display");
        const startHidden = row.querySelector(".manual-start-seconds");
        const endHidden = row.querySelector(".manual-end-seconds");
        const startMs = mmssToMs(startDisplay.value);
        const endMs = mmssToMs(endDisplay.value);
        if (startMs !== null) startHidden.value = (startMs / 1000).toFixed(1);
        if (endMs !== null) endHidden.value = (endMs / 1000).toFixed(1);
      });
    });
  }
});
