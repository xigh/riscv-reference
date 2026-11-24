// Live filter for tables: <input class="filter" data-table="ID"> hides the
// rows (tr[data-name]) of table#ID whose text does not contain the query.
(function () {
  document.querySelectorAll("input.filter[data-table]").forEach(function (input) {
    var table = document.getElementById(input.dataset.table);
    if (!table) return;
    var rows = Array.prototype.slice.call(table.querySelectorAll("tr[data-name]"));
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      rows.forEach(function (r) {
        r.hidden = q !== "" && r.textContent.toLowerCase().indexOf(q) < 0;
      });
    });
  });
})();
