const CODES = [
  ["CW101", "Removed endpoint", "error"],
  ["CW102", "Removed response field", "error"],
  ["CW103", "Incompatible type change", "error"],
  ["CW104", "Newly required request field", "error"],
  ["CW105", "Requiredness / nullability change", "error"],
  ["CW106", "Removed response status", "error"],
  ["CW107", "Breaking enum change", "error"],
  ["CW108", "Incompatible schema shape", "error"],
  ["CW110", "Compatible addition", "info"],
  ["CW201", "Confirmed schema collision", "error"],
  ["CW202", "Ambiguous repeated model name", "warning"],
  ["CW203", "Repeated model name, not exposed", "info"],
  ["CW204", "Component reference change", "warning"],
  ["RL101", "Blocking call in async endpoint", "warning"],
  ["RL102", "HTTP call without a timeout", "warning"],
  ["RL103", "Duplicate route/method registration", "warning"],
  ["RL104", "Conflicting operationId values", "warning"],
];

const FAQ = [
  ["does it run my app?", "It imports your FastAPI app to read the generated OpenAPI, in an isolated subprocess. It never serves traffic."],
  ["a change was intentional. now what?", "Run contractwatch baseline update and commit the new baseline."],
  ["can i silence a code?", "Yes: ignore = [\"CW110\"] under [tool.contractwatch] in pyproject.toml or contractwatch.toml."],
  ["does it need a server or account?", "No. It is a local CLI. Reports are saved on your machine."],
];

const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);

document.getElementById("codes").innerHTML = CODES.map(
  ([c, t, s]) => `<div class="row"><span class="code">${c}</span><span>${esc(t)}</span><span class="sev ${s}">${s}</span></div>`
).join("");

document.getElementById("faq").innerHTML = FAQ.map(
  ([q, a]) => `<details><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`
).join("");

const btn = document.getElementById("copy");
btn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(document.getElementById("cmd").textContent);
    btn.textContent = "copied";
  } catch {
    btn.textContent = "press ctrl+c";
  }
  setTimeout(() => (btn.textContent = "copy"), 1500);
});
