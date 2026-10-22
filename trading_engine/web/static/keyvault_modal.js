/**
 * Kite KeyVault Modal Logic
 * Zero-leakage credential management for Zerodha Kite Connect
 */

(function () {
    window.openVaultModal = function () {
        const modal = document.getElementById("vault-modal-backdrop");
        if (modal) {
            modal.style.display = "flex";
            const alertBox = document.getElementById("vault-alert-box");
            if (alertBox) alertBox.style.display = "none";
            checkVaultStatus();
        }
    };

    window.closeVaultModal = function () {
        const modal = document.getElementById("vault-modal-backdrop");
        if (modal) {
            modal.style.display = "none";
            // Clean inputs to avoid any secret lingering in DOM
            const keyInp = document.getElementById("vault-api-key");
            const tokInp = document.getElementById("vault-access-token");
            if (keyInp) keyInp.value = "";
            if (tokInp) tokInp.value = "";
        }
    };

    window.togglePasswordVisibility = function (inputId, btn) {
        const inp = document.getElementById(inputId);
        if (!inp) return;
        if (inp.type === "password") {
            inp.type = "text";
            btn.textContent = "🙈";
        } else {
            inp.type = "password";
            btn.textContent = "👁️";
        }
    };

    window.checkVaultStatus = async function () {
        try {
            const res = await fetch("/api/vault/kite-status");
            const data = await res.json();
            
            const dot = document.getElementById("vault-status-dot");
            const indicator = document.getElementById("vault-status-indicator");
            const text = document.getElementById("vault-status-text");

            if (data.configured) {
                if (dot) {
                    dot.className = "vault-status-dot configured";
                    dot.title = "Kite Connect Configured & Active";
                }
                if (indicator) {
                    indicator.className = "vault-status-indicator is-active";
                }
                if (text) {
                    text.textContent = "● Kite Connect is Configured & Active in KeyVault";
                }
            } else {
                if (dot) {
                    dot.className = "vault-status-dot unconfigured";
                    dot.title = "Kite Connect Not Configured";
                }
                if (indicator) {
                    indicator.className = "vault-status-indicator is-inactive";
                }
                if (text) {
                    const partial = [];
                    if (!data.api_key_set) partial.push("API Key missing");
                    if (!data.access_token_set) partial.push("Access Token missing");
                    text.textContent = `● KeyVault: ${partial.join(", ") || "No Kite credentials stored"}`;
                }
            }
        } catch (e) {
            console.warn("Could not check KeyVault status:", e);
        }
    };

    window.submitKiteVault = async function () {
        const keyInp = document.getElementById("vault-api-key");
        const tokInp = document.getElementById("vault-access-token");
        const alertBox = document.getElementById("vault-alert-box");
        const submitBtn = document.getElementById("vault-submit-btn");
        const spinner = document.getElementById("vault-btn-spinner");
        const btnText = document.getElementById("vault-btn-text");

        const apiKey = keyInp ? keyInp.value.trim() : "";
        const accessToken = tokInp ? tokInp.value.trim() : "";

        if (!apiKey || !accessToken) {
            showAlert("Please provide both the Kite API Key and Daily Access Token.", "error");
            return;
        }

        if (submitBtn) submitBtn.disabled = true;
        if (spinner) spinner.style.display = "inline-block";
        if (btnText) btnText.textContent = "Encrypting…";

        try {
            const res = await fetch("/api/vault/kite-credentials", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ api_key: apiKey, access_token: accessToken }),
            });

            const result = await res.json();

            if (res.ok && result.status === "success") {
                showAlert("✅ Credentials encrypted & saved to KeyVault! Live market connection hot-reloaded.", "success");
                // Immediately clear the inputs
                if (keyInp) keyInp.value = "";
                if (tokInp) tokInp.value = "";
                checkVaultStatus();
            } else {
                showAlert(`❌ Failed: ${result.message || "Could not save credentials"}`, "error");
            }
        } catch (e) {
            showAlert(`❌ Network error while saving to KeyVault: ${e.message}`, "error");
        } finally {
            if (submitBtn) submitBtn.disabled = false;
            if (spinner) spinner.style.display = "none";
            if (btnText) btnText.textContent = "🔒 Encrypt & Save to Vault";
        }
    };

    function showAlert(msg, type) {
        const alertBox = document.getElementById("vault-alert-box");
        if (alertBox) {
            alertBox.className = `vault-alert-box ${type}`;
            alertBox.textContent = msg;
            alertBox.style.display = "block";
        }
    }

    // Close on escape key
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            closeVaultModal();
        }
    });

    // Check status on DOM load
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", checkVaultStatus);
    } else {
        checkVaultStatus();
    }
})();
