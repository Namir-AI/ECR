"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const selector = document.querySelector("[data-multiple-towers]");
  const suffixRow = document.querySelector("[data-tower-suffix-row]");
  const suffixInput = document.querySelector("[data-tower-suffix]");
  if (!selector || !suffixRow || !suffixInput) {
    return;
  }

  const synchronizeSuffix = () => {
    const multiple = selector.value === "true";
    suffixRow.hidden = !multiple;
    suffixInput.required = multiple;
    if (!multiple) {
      suffixInput.value = "";
    }
  };

  selector.addEventListener("change", synchronizeSuffix);
  synchronizeSuffix();
});
