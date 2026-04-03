function sendMessage() {
  const input = document.getElementById("input");
  const text = input.value.trim();
  if (!text) return;

  const chatArea = document.getElementById("chatArea");

  // user message
  const userMsg = document.createElement("div");
  userMsg.className = "message user";
  userMsg.innerText = text;
  chatArea.appendChild(userMsg);

  input.value = "";

  fetch("/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ message: text })
  })
  .then(res => res.json())
  .then(data => {
    data.responses.forEach(r => {
      const botMsg = document.createElement("div");
      botMsg.className = "message bot";

      const result = r.result;

      botMsg.innerHTML = `
        <b>Claim:</b> ${r.claim}<br><br>
        <b>Verdict:</b> ${result.verdict}<br><br>
        ${result.explanation}
      `;

      chatArea.appendChild(botMsg);
    });

    chatArea.scrollTop = chatArea.scrollHeight;
  });
}

// Explorer search mode
function searchMode() {
  const query = document.getElementById("searchInput").value;

  fetch("/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ message: query })
  })
  .then(res => res.json())
  .then(data => {
    const container = document.getElementById("resultsContainer");
    container.innerHTML = "";

    data.responses.forEach(r => {
      const card = document.createElement("div");
      card.className = "fc-card";

      card.innerHTML = `
        <div class="fc-card-top">
          <div class="fc-claim-text">${r.claim}</div>
          <span class="fc-verdict-badge">${r.result.verdict}</span>
        </div>
        <div>${r.result.explanation}</div>
      `;

      container.appendChild(card);
    });
  });
}