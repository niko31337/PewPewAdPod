function msToMMSS(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function mmssToMs(text) {
  const trimmed = String(text).trim();
  if (!trimmed) return null;
  if (trimmed.includes(":")) {
    const [mPart, sPart] = trimmed.split(":");
    const m = parseInt(mPart, 10) || 0;
    const s = parseFloat(sPart) || 0;
    return Math.round((m * 60 + s) * 1000);
  }
  const seconds = parseFloat(trimmed);
  return Number.isNaN(seconds) ? null : Math.round(seconds * 1000);
}

function clampVal(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function buildTimelineRuler(ruler, durationMs) {
  const tickIntervalMs = durationMs <= 600000 ? 60000 : durationMs <= 3600000 ? 300000 : 600000;
  for (let t = 0; t <= durationMs; t += tickIntervalMs) {
    const tick = document.createElement("div");
    tick.className = "timeline-tick";
    tick.style.left = `${(t / durationMs) * 100}%`;
    tick.textContent = msToMMSS(t);
    ruler.appendChild(tick);
  }
}
