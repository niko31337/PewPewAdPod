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
