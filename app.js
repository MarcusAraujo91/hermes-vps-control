/**
 * hermes-status-hub Client Controller
 * Zero-Plaintext E2EE, Smart Polling (Page Visibility API) & Resilient API Client.
 */

// PILAR 2/4: motor criptográfico único (crypto-web.js). Zero reimplementação local.
const CryptoWeb = globalThis.HermesCryptoWeb;
if (!CryptoWeb && typeof window !== "undefined") {
  console.warn("crypto-web.js must be loaded before app.js");
}

const API_BASE = "https://hermes.tvaraujo.com";
let pollTimer = null;
let masterKeyWarned = false;
const POLL_INTERVAL = 10000; // 10s

function isMasterKeyConfigured() {
  try {
    if (!CryptoWeb) return false;
    CryptoWeb.resolveClientMasterKey();
    return true;
  } catch {
    return false;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

async function encryptZeroPlaintext(data, secret) {
  if (!CryptoWeb) throw new Error("Crypto engine not loaded");
  return CryptoWeb.encryptPayloadClient(data, secret);
}

// --- UI Helpers ---
function toast(msg, isError = false) {
  const c = document.getElementById("toasts");
  if (!c) return;
  const t = document.createElement("div");
  t.className = `toast ${isError ? "error" : "success"}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

function updateElement(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function stopSmartPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startSmartPolling() {
  if (pollTimer) clearInterval(pollTimer);
  if (!isMasterKeyConfigured()) {
    updateElement("lastRefresh", "Chave mestra ausente — painel bloqueado");
    if (!masterKeyWarned) {
      toast("Chave mestra não injetada pelo servidor. Operações bloqueadas.", true);
      masterKeyWarned = true;
    }
    return;
  }
  masterKeyWarned = false;
  refreshDashboard();
  pollTimer = setInterval(refreshDashboard, POLL_INTERVAL);
}

// --- API Client with E2EE Envelope ---
async function secureApiCall(op, payload = {}) {
  const fullPayload = { op, ...payload };
  if (!isMasterKeyConfigured()) {
    stopSmartPolling();
    return null;
  }

  try {
    const hex = await encryptZeroPlaintext(fullPayload);
    const res = await fetch(`${API_BASE}/api/vpsProxy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: { encrypted: hex } }),
    });

    if (!res.ok) {
      if (res.status === 401 || res.status === 403) {
        toast("Sessão expirada. Redirecionando para login...", true);
        window.location.href = "/unlock";
        return null;
      }
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();
    return data;
  } catch (err) {
    console.error("API error:", err);
    toast(`Erro na operação: ${err.message}`, true);
    return null;
  }
}

// --- Main Data Fetcher ---
async function refreshDashboard() {
  updateElement("lastRefresh", "Atualizando...");

  try {
    const [statusData, integrationsData, cronsData] = await Promise.all([
      secureApiCall("status"),
      secureApiCall("integrations"),
      secureApiCall("crons"),
    ]);

    const nowStr = new Date().toLocaleTimeString("pt-BR");
    updateElement("lastRefresh", nowStr);

    if (statusData) {
      const servicesEl = document.getElementById("services");
      if (servicesEl && statusData.services) {
        servicesEl.innerHTML = Object.entries(statusData.services)
          .map(([k, v]) => `
            <div class="card">
              <div class="card-title">${escapeHtml(k)}</div>
              <div class="detail ${v === 'active' || v === 'running' ? 'ok' : 'err'}">${escapeHtml(v)}</div>
            </div>
          `).join("");
      }

      if (statusData.vault_sync) updateElement("vaultSync", statusData.vault_sync);
      if (statusData.gateway_log) updateElement("gatewayLog", statusData.gateway_log);
    }

    if (integrationsData) {
      const intEl = document.getElementById("integrations");
      if (intEl && integrationsData.integrations) {
        intEl.innerHTML = Object.entries(integrationsData.integrations)
          .map(([k, v]) => `
            <div class="card">
              <div class="card-title">${escapeHtml(k)}</div>
              <div class="detail ${v?.status === 'connected' || v?.status === 'ok' ? 'ok' : 'err'}">
                ${escapeHtml(v?.status || 'offline')}
              </div>
            </div>
          `).join("");
      }
    }

    if (cronsData) {
      if (Array.isArray(cronsData.crons)) {
        updateElement("cronCount", cronsData.crons.length);
        const listEl = document.getElementById("cronList");
        if (listEl) listEl.textContent = JSON.stringify(cronsData.crons, null, 2);
      }
    }
  } catch (err) {
    updateElement("lastRefresh", "Erro");
    console.error("Dashboard refresh error:", err);
  }
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopSmartPolling();
    } else {
      startSmartPolling();
    }
  });

  // --- Event Listeners ---
  window.addEventListener("DOMContentLoaded", () => {
    if (typeof globalThis.initAntiInspect === "function") {
      globalThis.initAntiInspect();
    }

    startSmartPolling();

    document.getElementById("btnRefresh")?.addEventListener("click", refreshDashboard);

    document.getElementById("btnQr")?.addEventListener("click", async () => {
      const res = await secureApiCall("whatsapp_qr");
      const qrBox = document.getElementById("qrBox");
      if (qrBox && res) {
        if (typeof res.qr_png_base64 === "string" && res.qr_png_base64.length <= 200000 && /^[A-Za-z0-9+/=]+$/.test(res.qr_png_base64)) {
          qrBox.innerHTML = `<img src="data:image/png;base64,${res.qr_png_base64}" alt="QR Code WhatsApp" style="max-width:200px;border-radius:8px;"/>`;
        } else if (res.qr_ascii) {
          qrBox.innerHTML = `<pre style="font-size:8px;line-height:8px;">${escapeHtml(res.qr_ascii)}</pre>`;
        } else {
          qrBox.textContent = res.detail || "WhatsApp já conectado!";
        }
      }
    });

    document.getElementById("btnWaReset")?.addEventListener("click", async () => {
      if (confirm("Deseja resetar a sessão do WhatsApp?")) {
        const res = await secureApiCall("whatsapp_reset");
        toast(res?.ok ? "WhatsApp resetado com sucesso!" : "Falha ao resetar WhatsApp", !res?.ok);
        refreshDashboard();
      }
    });

    document.getElementById("btnRestart")?.addEventListener("click", async () => {
      if (confirm("Reiniciar o gateway VPS?")) {
        const res = await secureApiCall("restart_gateway");
        toast(res?.ok ? "Gateway reiniciado!" : "Falha ao reiniciar gateway", !res?.ok);
        refreshDashboard();
      }
    });

    document.getElementById("btnRunCron")?.addEventListener("click", async () => {
      const script = document.getElementById("cronScript")?.value?.trim();
      if (!script) return toast("Informe o nome do script cron", true);
      const res = await secureApiCall("run_cron", { script });
      toast(res?.ok ? `Cron '${script}' disparado!` : `Erro ao rodar cron: ${res?.detail}`, !res?.ok);
    });

    document.getElementById("btnPauseCron")?.addEventListener("click", async () => {
      const pattern = document.getElementById("cronPattern")?.value?.trim();
      if (!pattern) return toast("Informe o pattern do cron", true);
      const res = await secureApiCall("cron_pause", { pattern });
      toast(res?.ok ? `Cron '${pattern}' pausado!` : `Erro: ${res?.detail}`, !res?.ok);
    });
  });
}
