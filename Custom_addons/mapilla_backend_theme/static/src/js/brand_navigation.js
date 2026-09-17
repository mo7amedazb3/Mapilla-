/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState, useExternalListener } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useBus, useService } from "@web/core/utils/hooks";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { NavBar } from "@web/webclient/navbar/navbar";
import { AppsBar } from "@muk_web_appsbar/webclient/appsbar/appsbar";
// Explicit ordering: extend MuK's NavBar, never replace its menu service.
import "@muk_web_theme/webclient/navbar/navbar";

// Original, lightweight stroke icons. Matching changes presentation only: every
// app still comes from Odoo's permission-filtered, user-ordered menu service.
const APP_BRANDS = [
    ["mail.", "M4 4h16v12H9l-5 4V4 M8 8h8 M8 12h5", "التواصل"],
    ["calendar.", "M5 5h14v15H5z M8 3v4 M16 3v4 M5 10h14 M9 14h1 M14 14h1", "المواعيد"],
    ["contacts.", "M4 3h15v18H4z M19 7h2 M19 12h2 M19 17h2 M9 8a3 3 0 1 0 6 0a3 3 0 1 0-6 0 M7 18c0-6 10-6 10 0", "العلاقات"],
    ["sale.", "M4 20V10h4v10 M10 20V6h4v14 M16 20V3h4v17", "المبيعات"],
    ["costing_account.", "M5 3h14v18H5z M8 7h8 M8 11h2 M14 11h2 M8 15h2 M14 15h2 M8 18h2 M14 18h2", "التكاليف"],
    ["whatsapp_connector.", "M8 8V5a3 3 0 0 1 6 0v3 M5 8h12v4a6 6 0 0 1-12 0z M11 18v4 M18 5h3 M18 11h3", "التكامل"],
    ["whatsapp_integration.", "M5 17l-2 4 5-1a9 9 0 1 0-3-3 M8 7c0 5 4 9 9 9 M8 7l2-1 2 3-2 2 M17 16l1-2-3-2-2 2", "المحادثات"],
    ["spreadsheet_dashboard.", "M3 3h7v8H3z M14 3h7v5h-7z M3 15h7v6H3z M14 12h7v9h-7z", "التحليلات"],
    ["furniture_delivery_requests.", "M3 5h11v12H3z M14 9h4l3 4v4h-7 M6 17a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M16 17a2 2 0 1 0 4 0a2 2 0 1 0-4 0", "التسليم"],
    ["furniture_need_to_produce.", "M3 6l7-3 7 3-7 3z M3 6v8l7 3V9 M17 6v4 M15 14a4 4 0 1 0 0 8a4 4 0 1 0 0-8 M18 21l3 2", "تخطيط الاحتياجات"],
    ["furniture_assembly_requisitions.", "M6 3h12v18H6z M9 7h6 M9 11h6 M9 15h3 M3 7h3 M3 12h3 M3 17h3", "الأذونات"],
    ["furniture_mrp.", "M3 21V9l6 3V7l6 3V3h5v18z M6 16h2 M11 16h2 M16 16h2", "إدارة المصنع"],
    ["furniture_stage_replenishment.", "M4 7h16 M4 17h16 M8 4v6 M16 14v6", "Min / Max"],
    ["account.", "M4 21V9h16v12 M2 9l10-6 10 6z M8 12v6 M12 12v6 M16 12v6 M2 21h20", "المالية"],
    ["employee_advance_accounting.", "M3 10h18v11H3z M3 10V6l14-3v7 M16 14h5v4h-5z", "السلف"],
    ["purchase.", "M3 3h2l3 13h11l2-9H6 M9 20h1 M17 20h1 M10 3h6 M13 1v4", "المشتريات"],
    ["stock.", "M3 9l9-6 9 6v12H3z M7 21v-9h10v9 M7 16h10 M7 12h10", "المخازن"],
    ["mrp.", "M4 7l8-4 8 4v10l-8 4-8-4z M4 7l8 4 8-4 M12 11v10 M8 5l8 4", "التصنيع"],
    ["hr_attendance.", "M4 4h10v17H4z M8 17h2 M14 10h8 M18 6l4 4-4 4", "الحضور والانصراف"],
    ["hr_holidays.", "M5 5h14v15H5z M8 3v4 M16 3v4 M5 10h14 M8 15l3 3 5-5", "الإجازات"],
    ["hr.", "M8 7a4 4 0 1 0 8 0a4 4 0 1 0-8 0 M4 21v-2a8 8 0 0 1 16 0v2 M2 7a3 3 0 0 1 3-3 M19 4a3 3 0 0 1 3 3", "فريق العمل"],
    ["base.menu_management", "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z", "التطبيقات"],
    ["base.menu_administration", "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6", "الإعدادات"],
];

export function appBrand(app) {
    const match = APP_BRANDS.find(([prefix]) => (app.xmlid || "").startsWith(prefix));
    return match ? { path: match[1], caption: match[2] } : { path: null, caption: app.label };
}

export class MapillaAppIcon extends Component {
    static template = "mapilla_backend_theme.AppIcon";
    static props = { app: Object };
    get brand() { return appBrand(this.props.app); }
}

patch(NavBar, { components: { ...NavBar.components, MapillaAppIcon } });
patch(NavBar.prototype, {
    setup() {
        super.setup();
        this.mapillaAppsState = useDropdownState();
        this.mapillaCommand = useService("command");
        this.mapillaCompany = useService("company");
        useBus(this.env.bus, "MAPILLA:OPEN_APPS", () => this.mapillaAppsState.open());
    },
    mapillaAppBrand(app) { return appBrand(app); },
    mapillaSearchApps() {
        this.mapillaAppsState.close();
        this.mapillaCommand.openMainPalette({ searchValue: "/" });
    },
});

patch(AppsBar, { components: { ...AppsBar.components, MapillaAppIcon } });
patch(AppsBar.prototype, {
    setup() {
        super.setup();
        this.mapillaDrawer = useState({ top: 52 });
        const measure = () => {
            const navbar = document.querySelector('.o_navbar');
            this.mapillaDrawer.top = Math.max(0, navbar?.getBoundingClientRect().bottom || 0);
        };
        let observer;
        onMounted(() => {
            measure();
            const navbar = document.querySelector('.o_navbar');
            if (navbar) { observer = new ResizeObserver(measure); observer.observe(navbar); }
        });
        onWillUnmount(() => observer?.disconnect());
        useExternalListener(window, 'resize', measure);
    },
    mapillaOpenApps() {
        this.env.bus.trigger("MAPILLA:OPEN_APPS");
    },
});
