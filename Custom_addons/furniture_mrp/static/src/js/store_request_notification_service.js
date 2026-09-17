/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

export const HANDOFF_ACCEPTED_EVENT = "furniture_mrp:handoff_accepted";

export const furnitureStoreNotificationService = {
    dependencies: [
        "bus_service",
        "notification",
        "action",
        "orm",
        "multi_tab",
        "mail.sound_effects",
        "dialog",
    ],

    start(env, services) {
        const {
            bus_service,
            notification,
            action,
            orm,
            multi_tab: multiTab,
            dialog,
        } = services;
        const soundEffects = services["mail.sound_effects"];
        let refreshInFlight = false;
        let toastSequence = 0;
        const toastActions = new Map();
        const handoffNotifications = new Map();

        browser.addEventListener("click", (event) => {
            const target = event.target;
            const toast = target?.closest?.(".o_furniture_store_toast_clickable");
            if (
                !toast
                || target.closest?.(".o_notification_close")
                || target.closest?.(".o_notification_buttons")
            ) {
                return;
            }
            const actionToken = [...toast.classList].find((className) =>
                className.startsWith("o_furniture_store_action_")
            );
            toastActions.get(actionToken)?.();
        });

        const refreshMatchingRecord = (payload) => {
            const controller = action.currentController;
            const { resModel, resId, type } = controller?.props || {};
            const refreshResId = Number(payload.refresh_res_id || 0);
            if (
                refreshInFlight ||
                type !== "form" ||
                !refreshResId ||
                resModel !== payload.refresh_model ||
                Number(resId) !== refreshResId
            ) {
                return;
            }

            refreshInFlight = true;
            Promise.resolve(action.doAction({
                type: "ir.actions.client",
                tag: "soft_reload",
            }))
                // The toast stays available as a safe fallback if the view was
                // navigating or closing at the exact moment approval arrived.
                .catch(() => undefined)
                .finally(() => {
                    refreshInFlight = false;
                });
        };

        const playSound = (payload) => {
            if (!payload.play_sound || !multiTab.isOnMainTab()) {
                return;
            }
            try {
                soundEffects.play("new-message", { volume: 0.65 });
            } catch {
                // Browsers may block audio until the first user interaction.
            }
        };

        const handoffNotificationKey = (payload) => (
            String(
                payload?.handoff_notification_key
                || `lane:${Number(payload?.handoff_production_id || 0)}`
            )
        );

        const closeHandoffNotification = (payload, dismiss = false) => {
            const key = handoffNotificationKey(payload);
            const entry = handoffNotifications.get(key);
            if (!entry) {
                return;
            }
            entry.skipDismiss = !dismiss;
            entry.close?.();
        };

        const showHandoffNotification = (payload) => {
            const productionId = Number(payload.handoff_production_id || 0);
            if (!productionId) {
                return;
            }
            const notificationKey = handoffNotificationKey(payload);
            const stageHandoffId = Number(payload.handoff_id || 0);
            closeHandoffNotification(payload);
            const entry = {
                close: undefined,
                skipDismiss: false,
                accepting: false,
            };
            const acceptTransfer = async () => {
                if (entry.accepting) {
                    return;
                }
                entry.accepting = true;
                try {
                    await orm.call(
                        "furniture.mrp.production",
                        "action_accept_handoff_transfer",
                        [[productionId]],
                        stageHandoffId ? { stage_handoff_id: stageHandoffId } : {}
                    );
                    // Refresh mounted stage dashboards only after successful acceptance.
                    env.bus.trigger(HANDOFF_ACCEPTED_EVENT);
                    entry.skipDismiss = true;
                    entry.close?.();
                } catch (error) {
                    entry.accepting = false;
                    notification.add(
                        error?.data?.message || error?.message || _t("تعذر قبول التحويل."),
                        {
                            title: _t("لم يتم تنفيذ التحويل"),
                            type: "danger",
                            sticky: true,
                        }
                    );
                }
            };
            entry.close = notification.add(payload.message || "", {
                title: payload.title || _t("تحويل مرحلة جديد"),
                type: payload.type || "warning",
                sticky: true,
                className: "o_furniture_store_toast o_furniture_handoff_toast",
                buttons: [
                    {
                        name: _t("قبول التحويل"),
                        icon: "fa-check",
                        primary: true,
                        onClick: acceptTransfer,
                    },
                ],
                onClose: () => {
                    handoffNotifications.delete(notificationKey);
                    if (!entry.skipDismiss) {
                        orm.call(
                            "furniture.mrp.production",
                            "action_dismiss_handoff_notification",
                            [[productionId]],
                            stageHandoffId ? { stage_handoff_id: stageHandoffId } : {}
                        ).catch(() => undefined);
                    }
                },
            });
            handoffNotifications.set(notificationKey, entry);
            playSound(payload);
        };

        bus_service.subscribe("furniture_store_notification", (payload) => {
            if (payload.production_quality_warning) {
                if (multiTab.isOnMainTab()) {
                    dialog.add(AlertDialog, {
                        title: payload.title || _t("تحذير إنتاج"),
                        body: payload.message || "",
                        confirmLabel: _t("فهمت"),
                        confirmClass: "btn-warning",
                        contentClass: "o_furniture_production_warning_dialog",
                    });
                    playSound(payload);
                }
                return;
            }
            if (payload.handoff_event === "pending") {
                showHandoffNotification(payload);
                refreshMatchingRecord(payload);
                return;
            }
            if (payload.handoff_event === "resolved") {
                // Also cover acceptance from another tab or a production-order form.
                env.bus.trigger(HANDOFF_ACCEPTED_EVENT);
                closeHandoffNotification(payload);
                notification.add(payload.message || _t("تم قبول تحويل المرحلة."), {
                    title: payload.title || _t("تم قبول التحويل"),
                    type: "success",
                });
                refreshMatchingRecord(payload);
                return;
            }
            let closeNotification;
            let navigationInFlight = false;
            const openRecord = async () => {
                if (
                    navigationInFlight ||
                    !payload.action_model ||
                    !payload.action_res_id
                ) {
                    return;
                }
                navigationInFlight = true;
                try {
                    await action.doAction({
                        type: "ir.actions.act_window",
                        name: payload.title || _t("Warehouse Permission"),
                        res_model: payload.action_model,
                        res_id: Number(payload.action_res_id),
                        views: [[false, "form"]],
                        target: "current",
                    });
                    closeNotification?.();
                } finally {
                    navigationInFlight = false;
                }
            };
            const isActionable = Boolean(payload.action_model && payload.action_res_id);
            const actionToken = isActionable
                ? `o_furniture_store_action_${++toastSequence}`
                : "";
            if (isActionable) {
                toastActions.set(actionToken, openRecord);
            }
            // Build classes as an array.  A nested template literal previously
            // lost its leading space during asset minification and concatenated
            // both class names, so the click selector could never match.
            const toastClassName = [
                "o_furniture_store_toast",
                isActionable && "o_furniture_store_toast_clickable",
                actionToken,
            ].filter(Boolean).join(" ");

            closeNotification = notification.add(payload.message || "", {
                title: payload.title || _t("Warehouse Permission"),
                type: payload.type || "info",
                sticky: Boolean(payload.sticky),
                autocloseDelay: 6500,
                className: toastClassName,
                onClose: () => {
                    if (actionToken) {
                        toastActions.delete(actionToken);
                    }
                },
            });
            // The websocket event reaches every Odoo tab.  Only the main tab
            // should chime, otherwise one permission can produce many sounds.
            playSound(payload);
            refreshMatchingRecord(payload);
        });
        bus_service.start();
        orm.call(
            "furniture.mrp.production",
            "furniture_pending_handoff_notifications",
            []
        ).then((payloads) => {
            for (const payload of payloads || []) {
                showHandoffNotification(payload);
            }
        }).catch(() => undefined);
    },
};

registry.category("services").add(
    "furniture_store_notification",
    furnitureStoreNotificationService
);
