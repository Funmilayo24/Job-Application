document.querySelectorAll("[data-auto-submit]").forEach((select) => {
  select.addEventListener("change", () => select.form.submit());
});

document.querySelectorAll("[data-tailor-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    const button = form.querySelector("button");
    button.disabled = true;
    button.classList.add("is-loading");
  });
});
