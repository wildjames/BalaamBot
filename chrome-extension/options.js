const STORAGE_KEYS = ["serverUrl", "apiKey"];

// Populate fields from persistent storage on load.
chrome.storage.sync.get(STORAGE_KEYS, (data) => {
  document.getElementById("serverUrl").value = data.serverUrl ?? "";
  document.getElementById("apiKey").value = data.apiKey ?? "";
});

document.getElementById("saveBtn").addEventListener("click", () => {
  const status = document.getElementById("status");
  const serverUrl = document.getElementById("serverUrl").value.trim();
  const apiKey = document.getElementById("apiKey").value.trim();

  if (!serverUrl) {
    status.textContent = "Server URL is required.";
    status.className = "err";
    return;
  }

  try {
    new URL(serverUrl);
  } catch {
    status.textContent = "Server URL is not a valid URL.";
    status.className = "err";
    return;
  }

  if (!apiKey) {
    status.textContent = "API key is required.";
    status.className = "err";
    return;
  }

  chrome.storage.sync.set({serverUrl, apiKey}, () => {
    if (chrome.runtime.lastError) {
      status.textContent = "Error saving settings.";
      status.className = "err";
    } else {
      status.textContent = "Settings saved.";
      status.className = "ok";
    }
  });
});
