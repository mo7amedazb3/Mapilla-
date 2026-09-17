/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, xml } from "@odoo/owl";

export class WhatsappBackendUI extends Component {
    static template = xml`
        <div class="o_action w-100 h-100" style="overflow: hidden;">
            <iframe src="/whatsapp/chat" style="width: 100%; height: 100%; border: none;"></iframe>
        </div>
    `;
}

registry.category("actions").add("whatsapp_backend_ui", WhatsappBackendUI);
