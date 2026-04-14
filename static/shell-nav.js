(function () {
  var shell = document.querySelector(".app-shell");
  var btn = document.querySelector("[data-nav-toggle]");
  var backdrop = document.querySelector("[data-nav-backdrop]");
  if (!shell || !btn) return;

  function isMobileNav() {
    return window.matchMedia("(max-width: 900px)").matches;
  }

  function closeNav() {
    shell.classList.remove("nav-open");
    btn.setAttribute("aria-expanded", "false");
    document.body.classList.remove("nav-drawer-open");
    if (backdrop) backdrop.setAttribute("aria-hidden", "true");
  }

  function openNav() {
    shell.classList.add("nav-open");
    btn.setAttribute("aria-expanded", "true");
    if (isMobileNav()) document.body.classList.add("nav-drawer-open");
    if (backdrop) backdrop.setAttribute("aria-hidden", "false");
  }

  function toggleNav() {
    if (shell.classList.contains("nav-open")) closeNav();
    else openNav();
  }

  btn.addEventListener("click", function () {
    toggleNav();
  });

  if (backdrop) {
    backdrop.addEventListener("click", function () {
      closeNav();
    });
  }

  shell.querySelectorAll("#app-sidebar a.nav-link").forEach(function (link) {
    link.addEventListener("click", function () {
      if (isMobileNav()) closeNav();
    });
  });

  window.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeNav();
  });

  window.addEventListener("resize", function () {
    if (!isMobileNav()) {
      closeNav();
      document.body.classList.remove("nav-drawer-open");
    }
  });
})();
