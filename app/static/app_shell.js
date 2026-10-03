"use strict";
document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const sidebar = document.querySelector("#app-sidebar");
  const closeNavigation = () => {
    document.body.classList.remove("navigation-open");
    toggle?.setAttribute("aria-expanded", "false");
  };
  toggle?.addEventListener("click", () => {
    const open = document.body.classList.toggle("navigation-open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && document.body.classList.contains("navigation-open")) {
      closeNavigation(); toggle.focus();
    }
  });
  document.addEventListener("click", event => {
    if (toggle && document.body.classList.contains("navigation-open") && !sidebar.contains(event.target) && !toggle.contains(event.target)) closeNavigation();
  });
  document.querySelectorAll("[data-collapse]").forEach(button => {
    button.addEventListener("click", () => {
      const region = document.getElementById(button.getAttribute("aria-controls"));
      region.hidden = !region.hidden;
      button.setAttribute("aria-expanded", String(!region.hidden));
    });
  });
  document.querySelectorAll('.dashboard-filters').forEach(form => {
    form.addEventListener('submit', () => {
      const branch = form.elements.namedItem('branch_id');
      if (branch && branch.value === '') branch.disabled = true;
    });
  });
  const dialog = document.querySelector("[data-action-dialog]");
  if (!dialog) return;
  let trigger;
  document.querySelectorAll("[data-confirm-action]").forEach(button => {
    button.addEventListener("click", () => {
      trigger = button;
      dialog.querySelector("#action-title").textContent = button.dataset.confirmTitle;
      dialog.querySelector("#action-message").textContent = button.dataset.confirmMessage;
      dialog.querySelector("[data-action-form]").action = button.dataset.confirmAction;
      const cell = dialog.querySelector("[data-action-cell]");
      cell.disabled = !button.dataset.cellNo;
      cell.value = button.dataset.cellNo || "";
      dialog.querySelector("[data-action-submit]").textContent = button.dataset.confirmLabel;
      dialog.querySelector("[data-action-submit]").disabled = false;
      dialog.showModal();
      dialog.querySelector("[data-action-cancel]").focus();
    });
  });
  dialog.querySelector("[data-action-cancel]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => trigger?.focus());
  dialog.querySelector("[data-action-form]").addEventListener("submit", () => {
    dialog.querySelector("[data-action-submit]").disabled = true;
  });
});
