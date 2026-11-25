(() => {
    const actions = [
        ["show_approvals", "Show my approvals"],
        ["open_latest_case", "Open latest case"],
        ["start_new_case", "Start new case"],
    ];

    function mountQuickActions() {
        const composer = document.getElementById("message-composer");
        if (!composer || composer.querySelector("#supplyguard-quick-actions")) {
            return;
        }

        const toolbar = document.createElement("nav");
        toolbar.id = "supplyguard-quick-actions";
        toolbar.setAttribute("aria-label", "Case shortcuts");

        for (const [action, label] of actions) {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = label;
            button.addEventListener("click", () => {
                window.postMessage(`supplyguard:${action}`, window.location.origin);
            });
            toolbar.append(button);
        }
        composer.prepend(toolbar);
    }

    new MutationObserver(mountQuickActions).observe(document.body, {
        childList: true,
        subtree: true,
    });
    mountQuickActions();
})();
