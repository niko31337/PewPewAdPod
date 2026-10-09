document.addEventListener("DOMContentLoaded", () => {
  const table = document.getElementById("queue-table");
  if (!table) return;

  const tbody = table.querySelector("tbody");
  const selectAll = document.getElementById("select-all");
  const selectedCount = document.getElementById("selected-count");

  const updateSelectedCount = () => {
    const checked = tbody.querySelectorAll(".row-select:checked").length;
    if (selectedCount) selectedCount.textContent = `${checked} ausgewählt`;
  };

  if (selectAll) {
    selectAll.addEventListener("change", () => {
      tbody.querySelectorAll(".row-select").forEach((cb) => (cb.checked = selectAll.checked));
      updateSelectedCount();
    });
  }

  tbody.addEventListener("change", (event) => {
    if (!event.target.classList.contains("row-select")) return;
    if (!event.target.checked && selectAll) selectAll.checked = false;
    updateSelectedCount();
  });

  // Client-side only: reorders the visible rows for browsing convenience, does not
  // touch the "#" column (the real processing order) or send anything to the server.
  table.querySelectorAll("th.sortable").forEach((th, columnIndex) => {
    th.style.cursor = "pointer";
    th.dataset.sortDir = "";
    th.addEventListener("click", () => {
      const ascending = th.dataset.sortDir !== "asc";
      table.querySelectorAll("th.sortable").forEach((other) => (other.dataset.sortDir = ""));
      th.dataset.sortDir = ascending ? "asc" : "desc";

      const sortType = th.dataset.sortType;
      const rows = Array.from(tbody.querySelectorAll("tr")).filter((row) => row.cells.length > 1);
      rows.sort((rowA, rowB) => {
        const a = rowA.cells[columnIndex].dataset.value ?? rowA.cells[columnIndex].textContent.trim();
        const b = rowB.cells[columnIndex].dataset.value ?? rowB.cells[columnIndex].textContent.trim();
        let cmp;
        if (sortType === "num") {
          cmp = parseFloat(a) - parseFloat(b);
        } else if (sortType === "date") {
          cmp = (a || "").localeCompare(b || "");
        } else {
          cmp = a.localeCompare(b, "de", { sensitivity: "base" });
        }
        return ascending ? cmp : -cmp;
      });
      rows.forEach((row) => tbody.appendChild(row));
    });
  });
});


// ---- Playlist --------------------------------------------------------------
(function () {
  const playlist = document.querySelector('.playlist-card');
  if (!playlist) return;

  const filterBoxes = Array.from(playlist.querySelectorAll('.playlist-feed-select'));
  const rows = Array.from(playlist.querySelectorAll('.playlist-row'));
  const tableWrap = playlist.querySelector('[data-playlist-view="table"]');
  const listWrap = playlist.querySelector('[data-playlist-view="list"]');
  const count = playlist.querySelector('#playlist-feed-count');
  const summary = playlist.querySelector('#playlist-summary');

  function applyFilter() {
    const selected = new Set(filterBoxes.filter(box => box.checked).map(box => box.value));
    rows.forEach(row => {
      row.hidden = !selected.has(row.dataset.feedId);
    });
    if (count) {
      count.textContent = selected.size === filterBoxes.length ? '· alle Feeds' : `· ${selected.size} Feed(s)`;
    }
    if (summary) {
      const visible = rows.filter(row => !row.hidden).length;
      summary.textContent = `${visible} Episode(n) · Filter wirkt nur auf die Ansicht${selected.size === filterBoxes.length ? '' : ` · ${selected.size} Feed(s)`}`;
    }
  }

  function setView(view) {
    const isList = view === 'list';
    if (tableWrap) tableWrap.hidden = isList;
    if (listWrap) listWrap.hidden = !isList;
    playlist.querySelectorAll('.playlist-view-btn').forEach(btn => {
      btn.classList.toggle('playlist-view-active', btn.dataset.view === view);
    });
    try { localStorage.setItem('pewpewadpod-playlist-view', view); } catch (_) {}
  }

  filterBoxes.forEach(box => box.addEventListener('change', applyFilter));
  const selectAll = playlist.querySelector('#playlist-select-all');
  const selectNone = playlist.querySelector('#playlist-select-none');
  if (selectAll) selectAll.addEventListener('click', () => {
    filterBoxes.forEach(box => box.checked = true);
    applyFilter();
  });
  if (selectNone) selectNone.addEventListener('click', () => {
    filterBoxes.forEach(box => box.checked = false);
    applyFilter();
  });
  playlist.querySelectorAll('.playlist-view-btn').forEach(btn => {
    btn.addEventListener('click', () => setView(btn.dataset.view));
  });

  let initialView = 'table';
  try {
    const stored = localStorage.getItem('pewpewadpod-playlist-view');
    if (stored === 'list' || stored === 'table') initialView = stored;
  } catch (_) {}
  setView(initialView);
  applyFilter();
})();
