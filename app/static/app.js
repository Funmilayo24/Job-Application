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

document.querySelectorAll("[data-manual-search-form]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const confirmed = window.confirm(
      "Start a paid job search now? Manual searches are limited to control API costs."
    );
    if (!confirmed) {
      event.preventDefault();
      return;
    }
    const button = form.querySelector("button");
    button.disabled = true;
    button.textContent = "Queuing search...";
  });
});

document.querySelectorAll("[data-countdown-to]").forEach((element) => {
  const target = new Date(element.dataset.countdownTo);
  const update = () => {
    const remaining = target.getTime() - Date.now();
    if (remaining <= 0) {
      element.textContent = "Refresh the page to search again.";
      return;
    }
    const totalMinutes = Math.ceil(remaining / 60000);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    element.textContent = `Available in ${hours}h ${minutes}m.`;
  };
  update();
  window.setInterval(update, 60000);
});
