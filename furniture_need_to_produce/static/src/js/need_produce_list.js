/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";

/** Presentation only: native search, selection, exports and approval stay intact. */
export class NeedProduceListController extends ListController {
    static template = "furniture_need_to_produce.ListView";

    get className() {
        return `${super.className || ""} o_ntp_view`;
    }

    get ntpSummary() {
        // root.records contains loaded, unfolded rows, not every record in a
        // filtered/grouped result. The template labels this scope explicitly.
        const records = this.model.root.records || [];
        const models = new Set(
            records.map((record) => record.data.furniture_model_id?.[0]).filter(Boolean)
        );
        return {
            loaded: records.length,
            draft: records.filter((record) => record.data.state === "draft").length,
            models: models.size,
            total: this.nbTotal || 0,
            limited: Boolean(this.model.root.hasLimitedCount),
        };
    }
}

/** Keep grouping, arrows and + intact: parallel branches are not a flat route. */
export function tokenizeNeedProduceRoute(value) {
    return String(value || "")
        .split(/(انتظار أمر جارٍ \(\d+\)|مغطى من المخزون الجاهز|[()→+])/u)
        .map((value) => value.trim())
        .filter(Boolean)
        .map((text, index) => {
            let type = "stage";
            if (text === "→") {
                type = "arrow";
            } else if (text === "+") {
                type = "parallel";
            } else if (text === "(" || text === ")") {
                type = "group";
            } else if (text.startsWith("انتظار أمر جارٍ")) {
                type = "waiting";
            } else if (text === "مغطى من المخزون الجاهز") {
                type = "stock";
            }
            return { key: index, text, type };
        });
}

export class NeedProduceRouteField extends Component {
    static template = "furniture_need_to_produce.RouteField";
    static props = { ...standardFieldProps };

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    get tokens() {
        return tokenizeNeedProduceRoute(this.value);
    }

    get hasParallel() {
        return this.tokens.some((token) => token.type === "parallel");
    }
}

export class NeedProduceProductField extends Component {
    static template = "furniture_need_to_produce.ProductField";
    static props = { ...standardFieldProps };

    get productName() {
        return this.props.record.data[this.props.name]?.[1] || "—";
    }

    get pieceName() {
        return this.props.record.data.name || "";
    }
}

export class NeedProduceTimeField extends Component {
    static template = "furniture_need_to_produce.TimeField";
    static props = { ...standardFieldProps };

    format(value) {
        const number = Number(value);
        return Number.isFinite(number) ? String(Math.round(number * 100) / 100) : "—";
    }

    get hours() {
        return this.format(this.props.record.data[this.props.name]);
    }

    get days() {
        return this.format(this.props.record.data.critical_path_days);
    }
}

registry.category("fields").add("need_produce_route", {
    component: NeedProduceRouteField,
    supportedTypes: ["text", "char"],
});
registry.category("fields").add("need_produce_product", {
    component: NeedProduceProductField,
    supportedTypes: ["many2one"],
    fieldDependencies: [{ name: "name", type: "char" }],
});
registry.category("fields").add("need_produce_time", {
    component: NeedProduceTimeField,
    supportedTypes: ["float"],
    fieldDependencies: [{ name: "critical_path_days", type: "float" }],
});
registry.category("views").add("furniture_need_to_produce_list", {
    ...listView,
    Controller: NeedProduceListController,
});
