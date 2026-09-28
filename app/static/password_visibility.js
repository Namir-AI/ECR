"use strict";

document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-password-toggle]");
  if (!toggle) {
    return;
  }

  const input = document.getElementById(toggle.dataset.passwordTarget);
  if (!input) {
    return;
  }

  const willShow = input.type === "password";
  input.type = willShow ? "text" : "password";

  const label = willShow ? toggle.dataset.hideLabel : toggle.dataset.showLabel;
  toggle.textContent = willShow ? "Hide" : "Show";
  toggle.setAttribute("aria-label", label);
  toggle.setAttribute("title", label);
  toggle.setAttribute("aria-pressed", String(willShow));
});
