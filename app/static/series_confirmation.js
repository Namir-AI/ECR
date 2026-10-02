"use strict";

const initializeSeriesConfirmation = () => {
  const control = document.querySelector("[data-confirm-series]");
  const dialog = document.querySelector("[data-series-dialog]");
  if (!control || !dialog || control.dataset.confirmationInitialized) return;
  control.dataset.confirmationInitialized = "true";
  let accepted = control.value;
  let candidate = null;
  let accepting = false;

  const close = () => {
    candidate = null;
    dialog.close();
    if (!control.disabled) control.focus();
    else document.querySelector("[data-autosave-status]")?.focus();
  };
  const intercept = (event) => {
    if (accepting) return;
    if (dialog.open) {
      control.value = accepted;
      event.stopImmediatePropagation();
      return;
    }
    const requested = control.value;
    // The blank placeholder is for first entry, not a valid replacement Series.
    // Keep an existing selection so clearing it cannot bypass confirmation.
    if (accepted && requested === "") {
      control.value = accepted;
      event.stopImmediatePropagation();
      return;
    }
    if (requested === accepted) {
      event.stopImmediatePropagation();
      return;
    }
    if (!accepted) {
      accepted = requested; // First selection is ordinary entry, not a correction.
      return;
    }
    control.value = accepted; // Even a pending autosave sees only the old selection.
    event.stopImmediatePropagation();
    candidate = requested;
    dialog.querySelector("[data-series-current]").textContent = accepted;
    dialog.querySelector("[data-series-new]").textContent = requested || "Select…";
    dialog.showModal();
  };
  ["input", "change"].forEach(type => control.addEventListener(type, intercept, true));
  dialog.querySelector("[data-series-cancel]").addEventListener("click", close);
  dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
  dialog.addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const first = dialog.querySelector("[data-series-cancel]");
    const last = dialog.querySelector("[data-series-confirm]");
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  dialog.querySelector("[data-series-confirm]").addEventListener("click", () => {
    accepted = candidate;
    control.value = accepted;
    close();
    accepting = true;
    control.dispatchEvent(new Event("change", { bubbles: true }));
    accepting = false;
  });
  control.addEventListener("series-authoritative-restored", () => {
    accepted = control.value;
    if (dialog.open) close();
  });
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeSeriesConfirmation);
} else {
  initializeSeriesConfirmation();
}
