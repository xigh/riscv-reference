// Search: Pagefind UI, plus a deterministic "exact name" shortcut.
//
// The base URL is computed from this page's location, so the site works at a
// domain root, under /<repo>/ on GitHub Pages, or in a sub-directory of
// another site, without rebuilding.
(function () {
  var el = document.getElementById("search");
  if (!el || typeof PagefindUI === "undefined") return;
  var base = new URL(el.dataset.root || "./", location.href).pathname;
  var ui = new PagefindUI({
    element: "#search",
    baseUrl: base,
    bundlePath: base + "pagefind/",
    showImages: false,
    showSubResults: false,
    openFilters: ["type"],
    showEmptyFilters: false,
    resetStyles: false,
  });

  // Typing an exact instruction, CSR or extension name puts it first, and
  // Enter goes straight to it.
  var names = null;
  fetch(base + "static/names.json")
    .then(function (r) { return r.json(); })
    .then(function (j) { names = j; update(); });

  var box = document.createElement("div");
  box.className = "exact";
  box.hidden = true;
  el.appendChild(box);

  function input() { return el.querySelector(".pagefind-ui__search-input"); }
  function matches() {
    var i = input();
    if (!names || !i) return [];
    return names[i.value.trim().toLowerCase()] || [];
  }
  function update() {
    var m = matches();
    box.hidden = m.length === 0;
    box.innerHTML = "";
    m.forEach(function (e) {
      var a = document.createElement("a");
      a.href = base + e[1];
      a.innerHTML = "<span class=\"kind\"></span> <code></code> <span class=\"long\"></span>";
      a.querySelector(".kind").textContent = { inst: "Instruction", csr: "CSR", ext: "Extension" }[e[2]];
      a.querySelector("code").textContent = e[0];
      a.querySelector(".long").textContent = e[3];
      box.appendChild(a);
    });
    el.style.setProperty("--exact-h", box.hidden ? "0px" : box.offsetHeight + 4 + "px");
  }
  el.addEventListener("input", update);
  el.addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter") return;
    var m = matches();
    if (m.length) { ev.preventDefault(); location.href = base + m[0][1]; }
  });

  // ?q=term opens the page with that search already run (handy for links).
  var q = new URLSearchParams(location.search).get("q");
  if (q) {
    ui.triggerSearch(q);
    var i = input();
    if (i) i.value = q;
    update();
  }
})();
