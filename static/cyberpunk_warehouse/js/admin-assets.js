(() => {
    document.querySelectorAll("[data-image-preview-input]").forEach((input) => {
        const preview = input.closest("form")?.querySelector("[data-image-preview]");
        const image = preview?.querySelector("img");
        const info = preview?.querySelector("[data-image-info]");
        if (!preview || !image || !info) return;
        let objectUrl = null;
        input.addEventListener("change", () => {
            const file = input.files?.[0];
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            objectUrl = null;
            if (!file) {
                preview.hidden = true;
                image.removeAttribute("src");
                return;
            }
            if (!file.type.startsWith("image/")) {
                preview.hidden = true;
                return;
            }
            objectUrl = URL.createObjectURL(file);
            image.src = objectUrl;
            info.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB`;
            preview.hidden = false;
        });
    });

    document.querySelectorAll("form[data-confirm]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (!window.confirm(form.dataset.confirm)) event.preventDefault();
        });
    });
})();
