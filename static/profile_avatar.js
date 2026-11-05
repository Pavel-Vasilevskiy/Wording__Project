(() => {
    const picker = document.getElementById("avatar-file");
    const openButton = document.getElementById("change-avatar");
    const dialog = document.getElementById("avatar-preview-dialog");
    const preview = document.getElementById("avatar-preview");
    const cancelButton = document.getElementById("cancel-avatar");
    const form = document.getElementById("avatar-upload-form");
    if (!picker || !openButton || !dialog || !preview || !cancelButton || !form) return;

    let previewUrl = null;
    const clearPreview = () => {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = null;
        preview.removeAttribute("src");
        picker.value = "";
    };

    openButton.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => {
        const file = picker.files && picker.files[0];
        if (!file) return;
        const allowedTypes = ["image/jpeg", "image/png", "image/webp"];
        const extension = file.name.split(".").pop().toLowerCase();
        const allowedExtensions = ["jpg", "jpeg", "png", "webp"];
        if ((file.type && !allowedTypes.includes(file.type)) || !allowedExtensions.includes(extension)) {
            window.alert("Unsupported file format.");
            clearPreview();
            return;
        }
        if (file.size > 5 * 1024 * 1024) {
            window.alert("File is too large. Maximum size is 5 MB.");
            clearPreview();
            return;
        }
        previewUrl = URL.createObjectURL(file);
        preview.src = previewUrl;
        if (typeof dialog.showModal === "function") dialog.showModal();
        else if (window.confirm("Save this avatar?")) form.requestSubmit();
        else clearPreview();
    });

    cancelButton.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", clearPreview);
    form.addEventListener("submit", (event) => {
        if (!picker.files || !picker.files.length) {
            event.preventDefault();
            dialog.close();
        }
    });
})();
