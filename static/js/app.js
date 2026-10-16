(() => {
  const shell = document.getElementById("appShell");
  const toggle = document.getElementById("sidebarToggle");
  if (!shell || !toggle) return;

  const preference = localStorage.getItem("sportswear-sidebar-collapsed");
  if (preference === "true" && window.matchMedia("(min-width: 761px)").matches) {
    shell.classList.add("sidebar-collapsed");
  }

  toggle.addEventListener("click", () => {
    if (window.matchMedia("(max-width: 760px)").matches) {
      shell.classList.toggle("mobile-menu-open");
      return;
    }
    shell.classList.toggle("sidebar-collapsed");
    localStorage.setItem(
      "sportswear-sidebar-collapsed",
      shell.classList.contains("sidebar-collapsed"),
    );
  });

  shell.addEventListener("click", (event) => {
    if (event.target === shell && shell.classList.contains("mobile-menu-open")) {
      shell.classList.remove("mobile-menu-open");
    }
  });
})();
