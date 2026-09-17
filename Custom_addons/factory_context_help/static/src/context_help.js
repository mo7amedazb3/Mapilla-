/** @odoo-module **/
import { Component, onMounted } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

export class ContextHelpDialog extends Component {
    static template = "factory_context_help.Dialog";
    static components = { Dialog };
    static props = { close: Function, message: String, title: String };

    setup() {
        onMounted(() => this.labelDialog(this.modalRef?.el));
    }

    setModalRef(ref) {
        this.modalRef = ref;
    }

    labelDialog(element) {
        if (element) {
            element.setAttribute("aria-label", this.props.title);
            element.setAttribute("aria-modal", "true");
        }
    }
}

export class ContextHelpWidget extends Component {
    static template = "factory_context_help.Widget";
    static props = { ...standardWidgetProps, message: String, title: String };
}

registry.category("view_widgets").add("factory_context_help", {
    component: ContextHelpWidget,
    extractProps: ({ attrs }) => ({ message: attrs.message, title: attrs.title || "شرح" }),
});

/** Only explicit help buttons are handled. Never scan, hide or move app DOM.
 * Capture prevents a help click/keypress from also selecting/opening a card.
 * Dialog provides focus trapping, Escape, close, and return to the trigger.
 */
export function installContextHelp(root, open) {
    const find = (event) => event.target?.closest?.("button.o_factory_help_button[data-help-message]");
    const click = (event) => {
        const button = find(event);
        if (!button || button.disabled || !button.dataset.helpMessage?.trim()) return;
        event.preventDefault();
        event.stopPropagation();
        open({ title: button.dataset.helpTitle || "شرح", message: button.dataset.helpMessage });
    };
    const keydown = (event) => {
        if (find(event) && (event.key === "Enter" || event.key === " ")) {
            // Keep native button activation, but not kanban keyboard shortcuts.
            event.stopPropagation();
        }
    };
    root.addEventListener("click", click, true);
    root.addEventListener("keydown", keydown, true);
    return () => {
        root.removeEventListener("click", click, true);
        root.removeEventListener("keydown", keydown, true);
    };
}

registry.category("services").add("factory_context_help", {
    dependencies: ["dialog"],
    start(env, { dialog }) {
        const destroy = installContextHelp(document, props => dialog.add(ContextHelpDialog, props));
        return { destroy };
    },
});
