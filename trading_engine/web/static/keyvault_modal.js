/**
 * Multi-Broker KeyVault Modal Logic
 * Zero-leakage credential management for Zerodha Kite Connect & ICICI Direct Breeze API
 */

(function () {
    let currentBrokerTab = "kite";

    window.openVaultModal = function (tab) {
        const modal = document.getElementById("vault-modal-backdrop");
        if (modal) {
            modal.style.display = "flex";
            const alertBox = document.getElementById("vault-alert-box");
            if (alertBox) alertBox.style.display = "none";
            if (tab) switchBrokerTab(tab);
            checkVaultStatus();
        }
    };

    window.closeVaultModal = function () {
        const modal = document.getElementById("vault-modal-backdrop");
        if (modal) {
            modal.style.display = "none";
            // Clean inputs to avoid any secret lingering in DOM
            ["vault-api-key", "vault-access-token", "vault-breeze-key", "vault-breeze-secret", "vault-breeze-token", "vault-breeze-uid", "vault-breeze-pwd", "vault-breeze-totp"].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = "";
            });
        }
    };

    window.switchBrokerTab = function (tab) {
        currentBrokerTab = tab;
        const kiteTab = document.getElementById("tab-btn-kite");
        const breezeTab = document.getElementById("tab-btn-breeze");
        const kiteForm = document.getElementById("tab-content-kite");
        const breezeForm = document.getElementById("tab-content-breeze");

        if (tab === "kite") {
            if (kiteTab) kiteTab.className = "broker-tab-btn active";
            if (breezeTab) breezeTab.className = "broker-tab-btn";
            if (kiteForm) kiteForm.style.display = "block";
            if (breezeForm) breezeForm.style.display = "none";
        } else {
            if (kiteTab) kiteTab.className = "broker-tab-btn";
            if (breezeTab) breezeTab.className = "broker-tab-btn active";
            if (kiteForm) kiteForm.style.display = "none";
            if (breezeForm) breezeForm.style.display = "block";
        }
    };

    window.setActiveBroker = async function (broker) {
        try {
            const res = await fetch("/api/vault/active-broker", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ broker: broker })
            });
            const data = await res.json();
            if (res.ok && data.status === "success") {
                showAlert(`✅ Active broker switched to ${broker === 'breeze' ? 'ICICI Direct Breeze' : 'Zerodha Kite'}.`, "success");
                checkVaultStatus();
            } else {
                showAlert(`❌ Failed to switch broker: ${data.message || 'Unknown error'}`, "error");
            }
        } catch (e) {
            showAlert(`❌ Error switching broker: ${e.message}`, "error");
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
            const res = await fetch("/api/vault/broker-status");
            const data = await res.json();
            
            const dot = document.getElementById("vault-status-dot");
            const indicator = document.getElementById("vault-status-indicator");
            const text = document.getElementById("vault-status-text");

            const pillKite = document.getElementById("pill-active-kite");
            const pillBreeze = document.getElementById("pill-active-breeze");

            const active = data.active_broker || "kite";
            if (pillKite) pillKite.className = `active-pill-btn ${active === "kite" ? "selected" : ""}`;
            if (pillBreeze) pillBreeze.className = `active-pill-btn ${active === "breeze" ? "selected" : ""}`;

            const isCurrentConfigured = active === "breeze" ? data.breeze?.configured : data.kite?.configured;

            if (dot) {
                dot.className = `vault-status-dot ${isCurrentConfigured ? "configured" : "unconfigured"}`;
                dot.title = `Active: ${active.toUpperCase()} (${isCurrentConfigured ? 'Active' : 'Missing Token'})`;
            }

            if (indicator) {
                indicator.className = `vault-status-indicator ${isCurrentConfigured ? "is-active" : "is-inactive"}`;
            }

            if (text) {
                const brokerLabel = active === "breeze" ? "ICICI Direct Breeze" : "Zerodha Kite";
                if (active === "breeze" && data.breeze?.autologin_ready) {
                    text.innerHTML = `● Active Broker: <strong>${brokerLabel}</strong> <span style="color:#34d399;">(✨ 100% Zero-Touch Auto-Login Ready)</span>`;
                } else if (isCurrentConfigured) {
                    text.textContent = `● Active Broker: ${brokerLabel} (Configured & Live in KeyVault)`;
                } else {
                    text.textContent = `● Active Broker: ${brokerLabel} (Requires Daily Token / Credentials)`;
                }
            }

            // Maintain clean, universal 'Key Vault' label with informative tooltip
            const btnTexts = document.querySelectorAll(".vault-btn-text, #vault-btn-text");
            btnTexts.forEach(el => {
                el.textContent = "Key Vault";
            });
            const btnOpen = document.getElementById("btn-open-vault");
            if (btnOpen) {
                const brokerLabel = active === "breeze" ? "ICICI Direct Breeze" : "Zerodha Kite";
                btnOpen.title = `Key Vault (${brokerLabel} Active - Click to configure)`;
            }
        } catch (e) {
            console.warn("Could not check KeyVault broker status:", e);
        }
    };

    window.submitKiteVault = async function () {
        const keyInp = document.getElementById("vault-api-key");
        const tokInp = document.getElementById("vault-access-token");
        const submitBtn = document.getElementById("vault-kite-submit-btn");

        const apiKey = keyInp ? keyInp.value.trim() : "";
        const accessToken = tokInp ? tokInp.value.trim() : "";

        if (!apiKey || !accessToken) {
            showAlert("Please provide both the Kite API Key and Daily Access Token.", "error");
            return;
        }

        if (submitBtn) submitBtn.disabled = true;

        try {
            const res = await fetch("/api/vault/kite-credentials", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ api_key: apiKey, access_token: accessToken }),
            });
            const result = await res.json();

            if (res.ok && result.status === "success") {
                showAlert("✅ Zerodha Kite credentials encrypted & saved! Connection hot-reloaded.", "success");
                if (keyInp) keyInp.value = "";
                if (tokInp) tokInp.value = "";
                checkVaultStatus();
            } else {
                showAlert(`❌ Failed: ${result.message || "Could not save credentials"}`, "error");
            }
        } catch (e) {
            showAlert(`❌ Network error while saving Kite credentials: ${e.message}`, "error");
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    };

    window.submitBreezeVault = async function () {
        const keyInp = document.getElementById("vault-breeze-key");
        const secInp = document.getElementById("vault-breeze-secret");
        const uidInp = document.getElementById("vault-breeze-uid");
        const pwdInp = document.getElementById("vault-breeze-pwd");
        const totpInp = document.getElementById("vault-breeze-totp");
        const submitBtn = document.getElementById("vault-breeze-submit-btn");

        const apiKey = keyInp ? keyInp.value.trim() : "";
        const apiSecret = secInp ? secInp.value.trim() : "";
        const userId = uidInp ? uidInp.value.trim() : "";
        const password = pwdInp ? pwdInp.value.trim() : "";
        const totpKey = totpInp ? totpInp.value.trim() : "";

        if (!apiKey || !apiSecret) {
            showAlert("Please provide at least Breeze API Key and Secret Key.", "error");
            return;
        }

        if (submitBtn) submitBtn.disabled = true;
        showAlert("⏳ Encrypting credentials into KeyVault...", "success");

        try {
            const res = await fetch("/api/vault/breeze-autologin-credentials", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    api_key: apiKey,
                    api_secret: apiSecret,
                    user_id: userId,
                    password: password,
                    totp_key: totpKey,
                    trigger_now: Boolean(userId && password && totpKey)
                }),
            });
            const result = await res.json();

            if (res.ok && result.status === "success") {
                const auto = result.auto_login;
                if (auto && auto.success) {
                    showAlert("✅ 100% Zero-Touch Auto-Login Successful! Breeze session active.", "success");
                } else if (auto && !auto.success) {
                    showAlert(`⚠️ Credentials saved, but auto-login test note: ${auto.error || "Check details"}`, "error");
                } else {
                    showAlert("✅ Breeze credentials encrypted & saved to KeyVault!", "success");
                }
                checkVaultStatus();
            } else {
                showAlert(`❌ Failed: ${result.message || "Could not save credentials"}`, "error");
            }
        } catch (e) {
            showAlert(`❌ Network error while saving Breeze credentials: ${e.message}`, "error");
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    };

    window.triggerBreezeAutoLogin = async function () {
        const btn = document.getElementById("vault-breeze-autologin-btn");
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<span>⏳ Authenticating via TOTP...</span>`;
        }
        showAlert("⏳ Launching headless Playwright & generating live 6-digit TOTP...", "success");

        try {
            const res = await fetch("/api/broker/breeze/auto-login", { method: "POST" });
            const result = await res.json();
            if (res.ok && result.success) {
                showAlert("🎉 100% Zero-Touch Auto-Login Succeeded! Daily Breeze session active.", "success");
                checkVaultStatus();
            } else {
                showAlert(`❌ Auto-Login Failed: ${result.error || "Please check credentials and TOTP secret"}`, "error");
            }
        } catch (e) {
            showAlert(`❌ Auto-Login network error: ${e.message}`, "error");
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `<span>⚡ Test Auto-Login Now</span>`;
            }
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
