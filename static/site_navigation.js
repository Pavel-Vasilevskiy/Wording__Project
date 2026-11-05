(function () {
    "use strict";

    function closeNavigation(root, returnFocus) {
        const toggle = root.querySelector("[data-site-menu-toggle]");
        if (!toggle || root.dataset.open !== "true") return;
        root.dataset.open = "false";
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-label", "Відкрити меню");
        if (returnFocus) toggle.focus();
    }

    document.querySelectorAll("[data-site-navigation]").forEach((root) => {
        const toggle = root.querySelector("[data-site-menu-toggle]");
        const menu = root.querySelector("[data-site-menu]");
        if (!toggle || !menu) return;

        toggle.addEventListener("click", () => {
            const isOpen = root.dataset.open === "true";
            root.dataset.open = String(!isOpen);
            toggle.setAttribute("aria-expanded", String(!isOpen));
            toggle.setAttribute("aria-label", isOpen ? "Відкрити меню" : "Закрити меню");
            if (!isOpen) {
                const firstLink = menu.querySelector("a");
                if (firstLink && window.matchMedia("(max-width: 700px)").matches) firstLink.focus();
            }
        });

        menu.addEventListener("click", (event) => {
            if (event.target.closest("a")) closeNavigation(root, false);
        });

        document.addEventListener("click", (event) => {
            if (!root.contains(event.target)) closeNavigation(root, false);
        });

        root.addEventListener("keydown", (event) => {
            if (event.key === "Escape") closeNavigation(root, true);
        });
    });
}());
