"use strict";

// B1 is a visual projection only. No requests, stored derived readings or
// engineering acceptance judgments; the existing autosave owns persistence.
const initializeReadingInstruments = () => {
  const instruments = document.querySelectorAll("[data-reading-diagram]");
  if (!instruments.length) return;
  const maxShift = 18; // One third of the SVG radius: noticeable, contained.
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const read = (instrument, side) => {
    const name = `${instrument.dataset.readingDiagram}_${side}`;
    const input = instrument.querySelector(`[name="${name}"]`);
    const value = input ? input.value : instrument.querySelector(`[data-reading-value="${name}"]`).dataset.readingRaw;
    // Blank readings center the illustration only; inputs remain blank.
    const number = value.trim() === "" ? 0 : Number(value);
    return Number.isFinite(number) ? clamp(number, -1, 1) : 0;
  };
  const update = (instrument) => {
    const shiftY = clamp((read(instrument, "top") - read(instrument, "bottom")) / 2, -1, 1) * maxShift;
    const shiftX = clamp((read(instrument, "right") - read(instrument, "left")) / 2, -1, 1) * maxShift;
    // Translate each entire fixed line on one axis. Never tilt it or change
    // endpoint coordinates; the stationary circular clip contains both lines.
    instrument.querySelector("[data-reading-horizontal]").setAttribute("transform", `translate(0 ${shiftY})`);
    instrument.querySelector("[data-reading-vertical]").setAttribute("transform", `translate(${shiftX} 0)`);
  };
  const formatInput = (input) => {
    const decimal = input.value.match(/^[+-]?(?:\d+(?:\.(\d*))?|\.(\d+))$/);
    const fraction = decimal ? (decimal[1] || decimal[2] || "").replace(/0+$/, "") : "";
    // Formatting must never round an over-precise supplied value into validity.
    if (decimal && fraction.length <= 2 && input.validity.valid) {
      input.value = Number(input.value).toFixed(2);
    }
  };
  const unitSelect = document.querySelector('[name="de_nde_unit"]');
  const updateUnits = () => instruments.forEach((instrument) => {
    instrument.querySelector("[data-reading-unit]").textContent =
      (unitSelect ? unitSelect.value : instrument.dataset.readingUnitValue) || "—";
  });
  instruments.forEach((instrument) => {
    instrument.querySelectorAll(".reading-input").forEach(formatInput);
    instrument.querySelectorAll("[data-reading-value]").forEach((value) => {
      if (value.dataset.readingRaw !== "") value.textContent = Number(value.dataset.readingRaw).toFixed(2);
    });
    update(instrument);
  });
  updateUnits();
  ["input", "change"].forEach((type) => document.addEventListener(type, (event) => {
    if (event.target.matches(".reading-input")) update(event.target.closest("[data-reading-diagram]"));
    if (event.target === unitSelect) updateUnits();
  }));
  document.addEventListener("focusout", (event) => {
    if (event.target.matches(".reading-input")) formatInput(event.target);
  });
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeReadingInstruments);
} else {
  initializeReadingInstruments();
}
