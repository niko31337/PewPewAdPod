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

  playlist.querySelectorAll('.playlist-heard-checkbox').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      if (!checkbox.checked) return;
      const approved = window.confirm(
        'Diese Episode als bereits anderswo gehört markieren? Sie wird aus deinem Feed entfernt. ' +
        'Lokale Audio-, Korrektur-, Transkript- und Episoden-Cover-Dateien werden gelöscht. ' +
        'Die Episode wird dauerhaft von einer erneuten Verarbeitung ausgeschlossen.'
      );
      if (!approved) {
        checkbox.checked = false;
        return;
      }
      checkbox.disabled = true;
      checkbox.form.submit();
    });
  });

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


// Playlist multi-selection: checkboxes in table and list are paired by episode id.
(function () {
  const playlist = document.querySelector('.playlist-card');
  if (!playlist) return;

  const form = playlist.querySelector('#playlist-bulk-form');
  const boxes = Array.from(playlist.querySelectorAll('.playlist-episode-select'));
  const count = playlist.querySelector('#playlist-bulk-count');
  const selectVisibleButton = playlist.querySelector('#playlist-episodes-select-visible');
  const selectNoneButton = playlist.querySelector('#playlist-episodes-select-none');
  if (!form || boxes.length === 0) return;

  function selectedIds() {
    return new Set(boxes.filter(box => box.checked).map(box => box.dataset.episodeId));
  }

  function syncCount() {
    const selected = selectedIds();
    if (count) count.textContent = `${selected.size} ausgewählt`;
    form.querySelectorAll('[data-playlist-requires-selection]').forEach(button => {
      button.disabled = selected.size === 0;
    });
  }

  boxes.forEach(box => box.addEventListener('change', () => {
    const id = box.dataset.episodeId;
    boxes.filter(other => other.dataset.episodeId === id).forEach(other => {
      other.checked = box.checked;
    });
    syncCount();
  }));

  if (selectVisibleButton) selectVisibleButton.addEventListener('click', () => {
    const selectedFeeds = new Set(
      Array.from(playlist.querySelectorAll('.playlist-feed-select'))
        .filter(box => box.checked)
        .map(box => box.value)
    );
    const eligibleIds = new Set(
      Array.from(playlist.querySelectorAll('.playlist-row'))
        .filter(row => !row.hidden && selectedFeeds.has(row.dataset.feedId))
        .map(row => row.dataset.episodeId)
    );
    boxes.forEach(box => { box.checked = eligibleIds.has(box.dataset.episodeId); });
    syncCount();
  });

  if (selectNoneButton) selectNoneButton.addEventListener('click', () => {
    boxes.forEach(box => { box.checked = false; });
    syncCount();
  });

  form.addEventListener('submit', event => {
    if (form.dataset.confirmed === 'true') {
      delete form.dataset.confirmed;
      return;
    }
    const selected = selectedIds();
    if (selected.size === 0) {
      event.preventDefault();
      window.alert('Bitte mindestens eine Episode auswählen.');
      return;
    }
    if (event.submitter && event.submitter.value === 'heard_elsewhere') {
      event.preventDefault();
      const approved = window.confirm(
        `${selected.size} ausgewählte Episode(n) als anderswo gehört markieren? ` +
        'Sie werden aus dem Feed entfernt. Lokale Audio-, Korrektur-, Transkript- ' +
        'und Episoden-Cover-Dateien werden gelöscht. Die Episoden werden dauerhaft ' +
        'von einer erneuten Verarbeitung ausgeschlossen.'
      );
      if (approved) {
        form.dataset.confirmed = 'true';
        // Re-submit without triggering the confirmation handler a second time.
        form.requestSubmit(event.submitter);
      }
    }
  });

  syncCount();
})();
