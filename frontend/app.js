const API_BASE = "http://localhost:5001";

const jdText = document.getElementById("jdText");
const matchBtn = document.getElementById("matchBtn");
const matchError = document.getElementById("matchError");
const step2 = document.getElementById("step2");
const step3 = document.getElementById("step3");
const step4 = document.getElementById("step4");
const matchNote = document.getElementById("matchNote");
const gapTermsDiv = document.getElementById("gapTerms");
const projectListDiv = document.getElementById("projectList");
const generateBtn = document.getElementById("generateBtn");
const generateError = document.getElementById("generateError");
const summaryText = document.getElementById("summaryText");
const maxPagesInput = document.getElementById("maxPages");
const resultInfo = document.getElementById("resultInfo");
const downloadLink = document.getElementById("downloadLink");

let lastMatchResults = [];

matchBtn.addEventListener("click", async () => {
  matchError.textContent = "";
  const text = jdText.value.trim();

  if (!text) {
    matchError.textContent = "Please paste a job description first.";
    return;
  }

  matchBtn.disabled = true;
  matchBtn.textContent = "Analyzing...";

  try {
    const res = await fetch(`${API_BASE}/api/match`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd_text: text }),
    });

    const data = await res.json();

    if (!res.ok) {
      matchError.textContent = data.error || "Something went wrong analyzing the JD.";
      return;
    }

    lastMatchResults = data.ranked_projects;
    renderMatchResults(data);
    step2.classList.remove("hidden");
    step3.classList.remove("hidden");
    step2.scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    matchError.textContent = `Could not reach the backend at ${API_BASE}. Is the Flask server running? (${err.message})`;
  } finally {
    matchBtn.disabled = false;
    matchBtn.textContent = "Analyze & Match Projects";
  }
});

function renderMatchResults(data) {
  matchNote.textContent = data.note;

  gapTermsDiv.innerHTML = "";
  if (data.gap_terms && data.gap_terms.length > 0) {
    const label = document.createElement("div");
    label.textContent = "JD terms not found in your project bank (review manually):";
    label.style.marginBottom = "6px";
    gapTermsDiv.appendChild(label);
    data.gap_terms.forEach((term) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = term;
      gapTermsDiv.appendChild(chip);
    });
  }

  projectListDiv.innerHTML = "";
  data.ranked_projects.forEach((proj, index) => {
    const item = document.createElement("div");
    item.className = "project-item";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = proj.key;
    checkbox.id = `proj-${proj.key}`;
    // Auto-check the top 5 matches as a starting point -- user can adjust.
    checkbox.checked = index < 5 && proj.score > 0;

    const labelWrap = document.createElement("div");

    const titleLine = document.createElement("label");
    titleLine.htmlFor = `proj-${proj.key}`;
    titleLine.innerHTML = `<span class="project-title">${escapeHtml(proj.title)}</span><span class="project-score">score: ${proj.score}</span>`;

    const kwLine = document.createElement("div");
    kwLine.className = "project-keywords";
    kwLine.textContent = proj.matched_keywords.length
      ? `Matched: ${proj.matched_keywords.join(", ")}`
      : "No keyword overlap with this JD -- review manually before excluding.";

    labelWrap.appendChild(titleLine);
    labelWrap.appendChild(kwLine);

    item.appendChild(checkbox);
    item.appendChild(labelWrap);
    projectListDiv.appendChild(item);
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

generateBtn.addEventListener("click", async () => {
  generateError.textContent = "";
  resultInfo.innerHTML = "";
  downloadLink.classList.add("hidden");

  const selectedKeys = Array.from(
    projectListDiv.querySelectorAll("input[type=checkbox]:checked")
  ).map((cb) => cb.value);

  if (selectedKeys.length === 0) {
    generateError.textContent = "Select at least one project.";
    return;
  }

  const maxPages = parseInt(maxPagesInput.value, 10) || 2;

  generateBtn.disabled = true;
  generateBtn.textContent = "Generating (compiling LaTeX)...";

  try {
    const payload = {
      selected_project_keys: selectedKeys,
      max_pages: maxPages,
    };
    if (summaryText.value.trim()) {
      payload.summary = summaryText.value.trim();
    }

    const res = await fetch(`${API_BASE}/api/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await res.json();

    if (!res.ok) {
      generateError.textContent = data.error || "Resume generation failed.";
      if (data.log_tail) {
        console.error("LaTeX compile log tail:", data.log_tail);
      }
      return;
    }

    step4.classList.remove("hidden");

    if (data.warning) {
      resultInfo.innerHTML = `<div class="warning-box"><strong>Warning:</strong> ${escapeHtml(data.warning)}</div>`;
    } else {
      resultInfo.innerHTML = `<div class="success-box">Generated successfully: ${data.page_count} page(s), font ${data.font_size_used}pt (attempt ${data.compile_attempts} of the size ladder).</div>`;
    }

    const pdfBlob = base64ToBlob(data.pdf_base64, "application/pdf");
    const url = URL.createObjectURL(pdfBlob);
    downloadLink.href = url;
    downloadLink.classList.remove("hidden");

    step4.scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    generateError.textContent = `Could not reach the backend. (${err.message})`;
  } finally {
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate Resume PDF";
  }
});

function base64ToBlob(base64, mimeType) {
  const byteChars = atob(base64);
  const byteNumbers = new Array(byteChars.length);
  for (let i = 0; i < byteChars.length; i++) {
    byteNumbers[i] = byteChars.charCodeAt(i);
  }
  const byteArray = new Uint8Array(byteNumbers);
  return new Blob([byteArray], { type: mimeType });
}
