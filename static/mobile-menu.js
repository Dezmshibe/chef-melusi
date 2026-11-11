
(() => {
  "use strict";

  function initialiseMobileMenu() {
    const toggle = document.querySelector(".menu-toggle");
    const closeButton = document.querySelector(".menu-close");
    const menu = document.getElementById("mobile-menu");
    const backdrop = document.querySelector(".menu-backdrop");

    if (!toggle || !menu || !backdrop) {
      return;
    }

    let closeTimer = null;

    function openMenu() {
      if (closeTimer) {
        clearTimeout(closeTimer);
        closeTimer = null;
      }

      menu.inert = false;
      menu.setAttribute("aria-hidden", "false");
      backdrop.hidden = false;

      // Force the browser to register the closed position first.
      menu.getBoundingClientRect();

      menu.classList.add("is-open");
      backdrop.classList.add("is-visible");
      document.body.classList.add("menu-open");

      toggle.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-label", "Close navigation menu");

      if (closeButton) {
        closeButton.focus();
      }
    }

    function closeMenu(returnFocus = true) {
      menu.classList.remove("is-open");
      backdrop.classList.remove("is-visible");
      document.body.classList.remove("menu-open");

      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open navigation menu");

      if (closeTimer) {
        clearTimeout(closeTimer);
      }

      closeTimer = setTimeout(() => {
        menu.setAttribute("aria-hidden", "true");
        menu.inert = true;
        backdrop.hidden = true;
        closeTimer = null;
      }, 300);

      if (returnFocus) {
        toggle.focus();
      }
    }

    // Open and close using the hamburger button.
    toggle.addEventListener("click", () => {
      if (menu.classList.contains("is-open")) {
        closeMenu();
      } else {
        openMenu();
      }
    });

    // Close using the X button.
    if (closeButton) {
      closeButton.addEventListener("click", () => closeMenu());
    }

    // Close when the dark backdrop is tapped.
    backdrop.addEventListener("click", () => closeMenu());

    // Close after selecting a navigation link.
    menu.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => closeMenu(false));
    });

    // Close when Escape is pressed.
    document.addEventListener("keydown", (event) => {
      if (
        event.key === "Escape" &&
        menu.classList.contains("is-open")
      ) {
        closeMenu();
      }
    });

    // Reset the menu when switching to desktop width.
    window.addEventListener("resize", () => {
      if (
        window.innerWidth > 860 &&
        menu.classList.contains("is-open")
      ) {
        closeMenu(false);
      }
    });

    // Initial closed state.
    menu.classList.remove("is-open");
    menu.setAttribute("aria-hidden", "true");
    menu.inert = true;

    backdrop.classList.remove("is-visible");
    backdrop.hidden = true;

    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open navigation menu");
  }

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      initialiseMobileMenu,
      { once: true }
    );
  } else {
    initialiseMobileMenu();
  }
})();
