document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-loading-text]").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[type=submit]");
      if (btn) {
        btn.textContent = form.dataset.loadingText;
      }
      // Defer disabling to the next tick: disabling a named field *during* the submit
      // event strips it from the submitted form data (browsers only serialize enabled
      // controls), which would drop e.g. feed_id right before the request is sent.
      setTimeout(() => {
        if (btn) btn.disabled = true;
        form.querySelectorAll("select, input").forEach((el) => (el.disabled = true));
      }, 0);

      let bar = form.querySelector(".progress-bar-wrap");
      if (!bar) {
        bar = document.createElement("div");
        bar.className = "progress-bar-wrap";
        bar.innerHTML = '<div class="progress-bar-track"><div class="progress-bar-fill"></div></div>';
        form.appendChild(bar);
      }
      bar.classList.add("active");
    });
  });
});
