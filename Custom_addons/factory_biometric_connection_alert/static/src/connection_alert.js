/** @odoo-module **/

import { Component, onWillUnmount, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { session } from "@web/session";

const POLL_MS = 15000;
const REQUEST_TIMEOUT_MS = 10000;

export class BiometricConnectionAlert extends Component {
    static template = "factory_biometric_connection_alert.Alert";
    static props = {};

    setup() {
        this.state = useState({ devices: [], error: false, loading: false, dismissed: false });
        this.enabled = Boolean(session.factory_biometric_connection_alert);
        this.destroyed = false;
        this.request = null;
        this.timer = null;
        this.timeout = null;
        this.onFocus = () => this.refresh();
        onWillUnmount(() => {
            this.destroyed = true;
            browser.clearInterval(this.timer);
            browser.clearTimeout(this.timeout);
            this.request?.abort();
            window.removeEventListener("focus", this.onFocus);
            window.removeEventListener("online", this.onFocus);
        });
        if (this.enabled) {
            window.addEventListener("focus", this.onFocus);
            window.addEventListener("online", this.onFocus);
            this.timer = browser.setInterval(() => this.refresh(), POLL_MS);
            void this.refresh();
        }
    }

    lastSeen(value) {
        return value
            ? deserializeDateTime(value).toFormat("dd/MM/yyyy HH:mm:ss")
            : "لم يصل أي اتصال بعد";
    }

    dismiss() {
        // View-local only: navigation/polling keep it closed, but reopening the
        // database or reloading creates a fresh component and shows it again.
        this.state.dismissed = true;
    }

    async refresh() {
        if (!this.enabled || this.destroyed || this.request) {
            return;
        }
        this.state.loading = true;
        const request = rpc(
            "/web/dataset/call_kw/factory.biometric.device/get_connection_alert_status",
            {
                model: "factory.biometric.device",
                method: "get_connection_alert_status",
                args: [],
                kwargs: { context: user.context },
            },
            { silent: true }
        );
        this.request = request;
        this.timeout = browser.setTimeout(() => request.abort(), REQUEST_TIMEOUT_MS);
        try {
            const result = await request;
            if (this.destroyed) {
                return;
            }
            this.enabled = Boolean(result.enabled);
            this.state.devices = result.devices || [];
            this.state.error = false;
            if (!this.state.devices.length) {
                // A confirmed recovery ends this dismissal. A later outage
                // should warn again, even within the same open page.
                this.state.dismissed = false;
            }
            if (!this.enabled) {
                browser.clearInterval(this.timer);
            }
        } catch {
            if (!this.destroyed) {
                // An RPC failure is not evidence of device recovery. Preserve
                // any existing offline warning, and clearly mark stale status.
                this.state.error = true;
            }
        } finally {
            browser.clearTimeout(this.timeout);
            this.request = null;
            if (!this.destroyed) {
                this.state.loading = false;
            }
        }
    }
}

registry.category("main_components").add(
    "factory_biometric_connection_alert.Alert",
    { Component: BiometricConnectionAlert }
);
