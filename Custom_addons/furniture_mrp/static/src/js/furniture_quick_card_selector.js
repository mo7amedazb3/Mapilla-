/** @odoo-module **/

import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { Domain } from "@web/core/domain";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { getFieldDomain } from "@web/model/relational_model/utils";
import {
    Many2ManyTagsField,
    many2ManyTagsField,
} from "@web/views/fields/many2many_tags/many2many_tags_field";
import {
    Many2OneField,
    many2OneField,
} from "@web/views/fields/many2one/many2one_field";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

function normalizeLabel(value) {
    return (value || "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function normalizeSearchLabel(value) {
    return normalizeLabel(value)
        .normalize("NFKC")
        .replace(/[\u064B-\u065F\u0670\u0640]/g, "")
        .replace(/[أإآ]/g, "ا")
        .replace(/ة/g, "ه")
        .replace(/ى/g, "ي");
}

export class FurnitureQuickCardSelector extends Many2OneField {
    static template = "furniture_mrp.FurnitureQuickCardSelector";
    static props = {
        ...Many2OneField.props,
        quickNames: { type: Array, optional: true },
        quickLimit: { type: Number, optional: true },
        cardTitle: { type: String, optional: true },
        cardSubtitle: { type: String, optional: true },
        cardIcon: { type: String, optional: true },
        cardVariant: { type: String, optional: true },
        orderBy: { type: String, optional: true },
        alwaysShowMore: { type: Boolean, optional: true },
    };

    setup() {
        super.setup();
        this.notification = useService("notification");
        this.cardState = useState({
            items: [],
            expanded: false,
            loading: true,
            loadFailed: false,
            selectingId: false,
        });
        this.domainKey = "";
        this.requestNumber = 0;
        onWillStart(async () => {
            await this.loadItems(this.props);
        });
        onWillUpdateProps(async (nextProps) => {
            const nextDomainKey = JSON.stringify(this.getDomainForProps(nextProps));
            if (nextDomainKey !== this.domainKey) {
                await this.loadItems(nextProps);
            }
        });
    }

    get quickLimit() {
        return Math.max(this.props.quickLimit || 4, 1);
    }

    get title() {
        return this.props.cardTitle || this.string;
    }

    get subtitle() {
        return this.props.cardSubtitle || _t("اختار من البطاقات السريعة أو افتح باقي الاختيارات");
    }

    get icon() {
        return this.props.cardIcon || "fa-th-large";
    }

    get variantClass() {
        return `o_furniture_quick_selector--${this.props.cardVariant || "default"}`;
    }

    get selectedId() {
        const value = this.props.record.data[this.props.name];
        return value && value[0];
    }

    get quickItems() {
        return this.cardState.items.slice(0, this.quickLimit);
    }

    get overflowItems() {
        return this.cardState.items.slice(this.quickLimit);
    }

    get hasOverflow() {
        return this.overflowItems.length > 0;
    }

    get showMoreCard() {
        return Boolean(this.props.alwaysShowMore) || this.hasOverflow;
    }

    get overflowSelected() {
        return this.overflowItems.some((item) => item.id === this.selectedId);
    }

    getDomainForProps(props) {
        return Domain.and([
            getFieldDomain(props.record, props.name, props.domain),
        ]).toList(props.context);
    }

    async loadItems(props = this.props) {
        this.cardState.loading = true;
        this.cardState.loadFailed = false;
        const domain = this.getDomainForProps(props);
        const request = ++this.requestNumber;
        this.domainKey = JSON.stringify(domain);
        try {
            const relation = props.record.fields[props.name].relation;
            const records = await this.orm.searchRead(
                relation,
                domain,
                ["name", "display_name"],
                {
                    context: props.context,
                    order: props.orderBy || "name, id",
                }
            );
            if (request !== this.requestNumber) {
                return;
            }
            const items = records.map((record) => ({
                id: record.id,
                name: record.name || record.display_name,
                displayName: record.display_name || record.name,
            }));
            this.cardState.items = this.sortItems(items, props.quickNames || []);
        } catch (error) {
            if (request !== this.requestNumber) {
                return;
            }
            this.cardState.items = [];
            this.cardState.loadFailed = true;
            this.notification.add(
                _t("تعذر تحميل الاختيارات. حدّث الصفحة وحاول مرة أخرى."),
                {
                    title: _t("تعذر عرض البطاقات"),
                    type: "warning",
                }
            );
        } finally {
            if (request === this.requestNumber) {
                this.cardState.loading = false;
            }
        }
    }

    sortItems(items, names = this.props.quickNames) {
        const quickNames = Array.isArray(names)
            ? names
            : [];
        if (!quickNames.length) {
            return items;
        }

        const priorityByName = new Map(
            quickNames.map((name, index) => [normalizeLabel(name), index])
        );
        return items
            .map((item, originalIndex) => ({
                item,
                originalIndex,
                priority: priorityByName.has(normalizeLabel(item.name))
                    ? priorityByName.get(normalizeLabel(item.name))
                    : Number.MAX_SAFE_INTEGER,
            }))
            .sort((left, right) =>
                left.priority - right.priority ||
                left.originalIndex - right.originalIndex
            )
            .map(({ item }) => item);
    }

    isSelected(itemId) {
        return itemId === this.selectedId;
    }

    toggleMore() {
        this.cardState.expanded = !this.cardState.expanded;
    }

    async selectItem(item) {
        if (this.cardState.selectingId || this.isSelected(item.id)) {
            return;
        }
        this.cardState.selectingId = item.id;
        try {
            await super.updateRecord([item.id, item.displayName]);
            this.cardState.expanded = false;
        } finally {
            this.cardState.selectingId = false;
        }
    }
}

const furnitureQuickCardSelector = {
    ...many2OneField,
    component: FurnitureQuickCardSelector,
    supportedOptions: [
        ...many2OneField.supportedOptions,
        { name: "quick_names", type: "array", label: _t("Quick choices") },
        { name: "quick_limit", type: "number", label: _t("Quick choice limit") },
        { name: "card_title", type: "string", label: _t("Card title") },
        { name: "card_subtitle", type: "string", label: _t("Card subtitle") },
        { name: "card_icon", type: "string", label: _t("Card icon") },
        { name: "card_variant", type: "string", label: _t("Card variant") },
        { name: "order_by", type: "string", label: _t("Card ordering") },
        { name: "always_show_more", type: "boolean", label: _t("Always show More") },
    ],
    extractProps(fieldInfo, dynamicInfo) {
        const props = many2OneField.extractProps(fieldInfo, dynamicInfo);
        const { options } = fieldInfo;
        return {
            ...props,
            quickNames: options.quick_names || [],
            quickLimit: options.quick_limit || 4,
            cardTitle: options.card_title || "",
            cardSubtitle: options.card_subtitle || "",
            cardIcon: options.card_icon || "",
            cardVariant: options.card_variant || "default",
            orderBy: options.order_by || "name, id",
            alwaysShowMore: Boolean(options.always_show_more),
        };
    },
};

registry.category("fields").add(
    "furniture_quick_card_selector",
    furnitureQuickCardSelector
);

export class FurnitureMultiCardSelector extends Many2ManyTagsField {
    static template = "furniture_mrp.FurnitureMultiCardSelector";
    static props = {
        ...Many2ManyTagsField.props,
        quickNames: { type: Array, optional: true },
        quickLimit: { type: Number, optional: true },
        cardTitle: { type: String, optional: true },
        cardSubtitle: { type: String, optional: true },
        cardIcon: { type: String, optional: true },
        cardVariant: { type: String, optional: true },
        orderBy: { type: String, optional: true },
        alwaysShowMore: { type: Boolean, optional: true },
        singleSelection: { type: Boolean, optional: true },
        searchable: { type: Boolean, optional: true },
        searchPlaceholder: { type: String, optional: true },
    };

    setup() {
        super.setup();
        this.notification = useService("notification");
        this.cardState = useState({
            items: [],
            expanded: false,
            loading: true,
            loadFailed: false,
            selectingId: false,
            searchQuery: "",
        });
        this.domainKey = "";
        onWillStart(async () => {
            await this.loadItems(this.props);
        });
        onWillUpdateProps(async (nextProps) => {
            const nextDomainKey = JSON.stringify(this.getDomainForProps(nextProps));
            if (nextDomainKey !== this.domainKey) {
                await this.loadItems(nextProps);
            }
        });
    }

    get quickLimit() {
        return Math.max(this.props.quickLimit || 4, 1);
    }

    get title() {
        return this.props.cardTitle || this.string;
    }

    get subtitle() {
        return this.props.cardSubtitle || _t("اختار منتجًا واحدًا أو أكثر");
    }

    get icon() {
        return this.props.cardIcon || "fa-cubes";
    }

    get variantClass() {
        return `o_furniture_quick_selector--${this.props.cardVariant || "default"}`;
    }

    get selectedIds() {
        return new Set(
            this.props.record.data[this.props.name].records
                .map((record) => record.resId)
                .filter(Boolean)
        );
    }

    get readonlyDisplayName() {
        return this.props.record.data[this.props.name].records
            .map((record) => record.data.display_name)
            .filter(Boolean)
            .join("، ");
    }

    get searchPlaceholder() {
        return this.props.searchPlaceholder || _t("ابحث في الاختيارات...");
    }

    get normalizedSearchQuery() {
        return normalizeSearchLabel(this.cardState.searchQuery);
    }

    get isSearching() {
        return Boolean(this.normalizedSearchQuery);
    }

    get filteredItems() {
        const query = this.normalizedSearchQuery;
        if (!query) {
            return this.cardState.items;
        }
        return this.cardState.items.filter((item) =>
            normalizeSearchLabel(`${item.name || ""} ${item.displayName || ""}`)
                .includes(query)
        );
    }

    get quickItems() {
        return this.isSearching
            ? this.filteredItems
            : this.cardState.items.slice(0, this.quickLimit);
    }

    get overflowItems() {
        return this.cardState.items.slice(this.quickLimit);
    }

    get hasOverflow() {
        return this.overflowItems.length > 0;
    }

    get showMoreCard() {
        return !this.isSearching && (
            Boolean(this.props.alwaysShowMore) || this.hasOverflow
        );
    }

    get overflowSelected() {
        return this.overflowItems.some((item) => this.isSelected(item.id));
    }

    getDomainForProps(props) {
        return Domain.and([
            getFieldDomain(props.record, props.name, props.domain),
        ]).toList(props.context);
    }

    async loadItems(props) {
        this.cardState.loading = true;
        this.cardState.loadFailed = false;
        const domain = this.getDomainForProps(props);
        this.domainKey = JSON.stringify(domain);
        try {
            const relation = props.record.fields[props.name].relation;
            const records = await this.orm.searchRead(
                relation,
                domain,
                ["name", "display_name"],
                {
                    context: props.context,
                    order: props.orderBy || "name, id",
                }
            );
            const items = records.map((record) => ({
                id: record.id,
                name: record.name || record.display_name,
                displayName: record.display_name || record.name,
            }));
            this.cardState.items = this.sortItems(items, props.quickNames || []);
        } catch (error) {
            this.cardState.items = [];
            this.cardState.loadFailed = true;
            this.notification.add(
                _t("تعذر تحميل المنتجات. حدّث الصفحة وحاول مرة أخرى."),
                {
                    title: _t("تعذر عرض بطاقات المنتجات"),
                    type: "warning",
                }
            );
        } finally {
            this.cardState.loading = false;
        }
    }

    sortItems(items, quickNames) {
        if (!quickNames.length) {
            return items;
        }
        const priorityByName = new Map(
            quickNames.map((name, index) => [normalizeLabel(name), index])
        );
        return items
            .map((item, originalIndex) => ({
                item,
                originalIndex,
                priority: priorityByName.has(normalizeLabel(item.name))
                    ? priorityByName.get(normalizeLabel(item.name))
                    : Number.MAX_SAFE_INTEGER,
            }))
            .sort((left, right) =>
                left.priority - right.priority ||
                left.originalIndex - right.originalIndex
            )
            .map(({ item }) => item);
    }

    isSelected(itemId) {
        return this.selectedIds.has(itemId);
    }

    toggleMore() {
        this.cardState.expanded = !this.cardState.expanded;
    }

    onSearchInput(event) {
        this.cardState.searchQuery = event.currentTarget.value || "";
        if (this.cardState.searchQuery) {
            this.cardState.expanded = false;
        }
    }

    clearSearch() {
        this.cardState.searchQuery = "";
    }

    async toggleItem(item) {
        if (this.cardState.selectingId !== false) {
            return;
        }
        this.cardState.selectingId = item.id;
        try {
            const list = this.props.record.data[this.props.name];
            const selectedRecord = list.records.find(
                (record) => record.resId === item.id
            );
            if (this.props.singleSelection) {
                if (selectedRecord && this.selectedIds.size === 1) {
                    return;
                }
                for (const record of [...list.records]) {
                    if (record.resId !== item.id) {
                        await list.forget(record);
                    }
                }
                if (!selectedRecord) {
                    await this.update([
                        { id: item.id, display_name: item.displayName },
                    ]);
                }
                return;
            }
            if (selectedRecord) {
                await list.forget(selectedRecord);
            } else {
                await this.update([{ id: item.id, display_name: item.displayName }]);
            }
        } finally {
            this.cardState.selectingId = false;
        }
    }
}

const furnitureMultiCardSelector = {
    ...many2ManyTagsField,
    component: FurnitureMultiCardSelector,
    supportedOptions: [
        ...many2ManyTagsField.supportedOptions,
        { name: "quick_names", type: "array", label: _t("Quick choices") },
        { name: "quick_limit", type: "number", label: _t("Quick choice limit") },
        { name: "card_title", type: "string", label: _t("Card title") },
        { name: "card_subtitle", type: "string", label: _t("Card subtitle") },
        { name: "card_icon", type: "string", label: _t("Card icon") },
        { name: "card_variant", type: "string", label: _t("Card variant") },
        { name: "order_by", type: "string", label: _t("Card ordering") },
        { name: "always_show_more", type: "boolean", label: _t("Always show More") },
        { name: "single_selection", type: "boolean", label: _t("Single selection") },
        { name: "searchable", type: "boolean", label: _t("Searchable cards") },
        { name: "search_placeholder", type: "string", label: _t("Search placeholder") },
    ],
    extractProps(fieldInfo, dynamicInfo) {
        const props = many2ManyTagsField.extractProps(fieldInfo, dynamicInfo);
        const { options } = fieldInfo;
        return {
            ...props,
            quickNames: options.quick_names || [],
            quickLimit: options.quick_limit || 4,
            cardTitle: options.card_title || "",
            cardSubtitle: options.card_subtitle || "",
            cardIcon: options.card_icon || "",
            cardVariant: options.card_variant || "default",
            orderBy: options.order_by || "name, id",
            alwaysShowMore: Boolean(options.always_show_more),
            singleSelection: Boolean(options.single_selection),
            searchable: Boolean(options.searchable),
            searchPlaceholder: options.search_placeholder || "",
        };
    },
};

registry.category("fields").add(
    "furniture_multi_card_selector",
    furnitureMultiCardSelector
);

export class FurnitureDimensionToggle extends Component {
    static template = "furniture_mrp.FurnitureDimensionToggle";
    static props = { ...standardFieldProps };

    get expanded() {
        return Boolean(this.props.record.data[this.props.name]);
    }

    get label() {
        return this.props.record.data.dimension_label || _t("المقاس");
    }

    async toggle() {
        await this.props.record.update({
            [this.props.name]: !this.expanded,
        });
    }
}

registry.category("fields").add("furniture_dimension_toggle", {
    component: FurnitureDimensionToggle,
    supportedTypes: ["boolean"],
    relatedFields: () => [{ name: "dimension_label", type: "char" }],
});

export class FurnitureTouchSelection extends Component {
    static template = "furniture_mrp.FurnitureTouchSelection";
    static props = { ...standardFieldProps };

    setup() {
        this.touchState = useState({ selectingValue: false });
    }

    get options() {
        return this.props.record.fields[this.props.name].selection.filter(
            ([value, label]) => value !== false && label !== ""
        );
    }

    get label() {
        return this.props.record.fields[this.props.name].string;
    }

    isSelected(value) {
        return this.props.record.data[this.props.name] === value;
    }

    async selectValue(value) {
        if (
            this.props.readonly ||
            this.touchState.selectingValue !== false ||
            this.isSelected(value)
        ) {
            return;
        }
        this.touchState.selectingValue = value;
        try {
            await this.props.record.update({ [this.props.name]: value });
        } finally {
            this.touchState.selectingValue = false;
        }
    }
}

registry.category("fields").add("furniture_touch_selection", {
    component: FurnitureTouchSelection,
    displayName: _t("Touch selection buttons"),
    supportedTypes: ["selection"],
    extractProps(_fieldInfo, dynamicInfo) {
        // Keep the buttons active in a readonly-rendered list row.  The real
        // form/view readonly state is still propagated through dynamicInfo.
        return { readonly: dynamicInfo.readonly };
    },
});
