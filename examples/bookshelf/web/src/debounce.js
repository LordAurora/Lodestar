// Small utilities for the search box.

/** Delay calling `fn` until `wait` ms have passed without another call. */
export function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/** Highlight occurrences of `term` in `text` with <mark> tags. */
export const highlight = (text, term) =>
  term ? text.replace(new RegExp(`(${term})`, "gi"), "<mark>$1</mark>") : text;
