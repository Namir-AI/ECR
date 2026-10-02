"use strict";

const initializeNonnegativeDecimals = () => {
  // Plain decimal editing, never numeric coercion/abs() or locale conversion.
  document.querySelectorAll("[data-nonnegative-decimal], [data-nonnegative-integer]").forEach((field) => {
    const decimal = field.hasAttribute("data-nonnegative-integer") ? /^\d*$/ : /^(?:\d+(?:\.\d*)?|\.\d*|)$/;
    if (field.dataset.decimalGuardInitialized === "true") return;
    field.dataset.decimalGuardInitialized = "true";
    let lastValue = decimal.test(field.value) ? field.value : "";

    field.addEventListener("keydown", (event) => {
      // Preserve navigation, deletion, selection and clipboard shortcuts.
      if (!event.ctrlKey && !event.metaKey && (["-", "+", "e", "E"].includes(event.key) || (field.hasAttribute("data-nonnegative-integer") && event.key === "."))) {
        event.preventDefault();
      }
    });
    field.addEventListener("beforeinput", (event) => {
      // Covers mobile keyboards, IME, replacement text and multi-character edits.
      if (event.data !== null && !decimal.test(event.data)) event.preventDefault();
    });
    field.addEventListener("paste", (event) => {
      // Reject the whole invalid paste. Never strip a minus into a positive number.
      const text = event.clipboardData?.getData("text/plain");
      if (text === undefined || !decimal.test(text)) event.preventDefault();
    });
    field.addEventListener("drop", (event) => {
      const text = event.dataTransfer?.getData("text/plain");
      if (text === undefined || !decimal.test(text)) event.preventDefault();
    });
    field.addEventListener("input", (event) => {
      // Fallback for browsers/input methods without cancellable beforeinput.
      // A lone decimal point is a permitted editing prefix, not saved numeric data.
      const badFragment = typeof event.data === "string" && !decimal.test(event.data);
      const badNativeInput = field.validity.badInput && event.data !== "." &&
        !event.inputType?.startsWith("delete");
      if (badFragment || badNativeInput || !decimal.test(field.value)) {
        field.value = lastValue;
        event.stopImmediatePropagation();
        return;
      }
      lastValue = field.value;
    }, true);
  });
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeNonnegativeDecimals);
} else {
  initializeNonnegativeDecimals();
}
