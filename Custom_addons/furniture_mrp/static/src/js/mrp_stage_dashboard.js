/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { HANDOFF_ACCEPTED_EVENT } from "@furniture_mrp/js/store_request_notification_service";

const STAGE_DEFINITIONS = [
    { code: "priming", label: "التقديم", iconClass: "fa fa-paint-brush" },
    { code: "painting", label: "تصنيع دهانات", iconClass: "fa fa-tint" },
    { code: "carpentry", label: "التجميع", iconClass: "fa fa-cubes" },
    { code: "bases", label: "القواعد", iconClass: "fa fa-th-large" },
    { code: "finishing", label: "التجهيز", iconClass: "fa fa-wrench" },
    { code: "upholstery", label: "الكسوة", iconClass: "fa fa-cube" },
    { code: "tailoring", label: "التفصيل", iconClass: "fa fa-scissors" },
    { code: "packaging", label: "التغليف", iconClass: "fa fa-archive" },
];

const STAGE_DEFINITION_BY_CODE = Object.fromEntries(
    STAGE_DEFINITIONS.map((stage) => [stage.code, stage])
);

const ORDER_SUPERVISOR_STAGE_CODES = new Set([
    "tailoring",
    "upholstery",
    "packaging",
]);

const PRODUCTION_WARNING_SOURCE_STAGE_CODES = new Set([
    "painting",
    "carpentry",
    "bases",
    "finishing",
    "upholstery",
    "packaging",
]);

const NUMBER_FIELDS = [
    "order_count",
    "planned_qty",
    "started_qty",
    "working_qty",
    "quality_qty",
    "completed_qty",
    "not_started_qty",
    "remaining_qty",
    "progress",
];

const ORDER_QUANTITY_FIELDS = NUMBER_FIELDS.filter(
    (fieldName) => fieldName !== "order_count" && fieldName !== "progress"
);

const MATERIAL_QUANTITY_FORMATTER = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 3,
});

function asNumber(value) {
    const number = Number(value ?? 0);
    return Number.isFinite(number) ? number : 0;
}

function clampProgress(value) {
    return Math.max(0, Math.min(100, asNumber(value)));
}

function parseServerDate(value) {
    if (!value) {
        return false;
    }
    const text = String(value);
    const normalized = text.includes("T") ? text : `${text.replace(" ", "T")}Z`;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? false : date;
}

function normalizeIcon(rawIcon, fallbackIconClass) {
    const icon = String(rawIcon || "").trim();
    if (!icon) {
        return { iconClass: fallbackIconClass, iconText: "" };
    }
    if (icon === "fa" || icon.startsWith("fa ") || icon.includes(" fa-")) {
        return { iconClass: icon, iconText: "" };
    }
    if (icon.startsWith("fa-")) {
        return { iconClass: `fa ${icon}`, iconText: "" };
    }
    return { iconClass: fallbackIconClass, iconText: icon };
}

function normalizeStage(rawStage, definition) {
    const raw = rawStage || {};
    const icon = normalizeIcon(raw.icon, definition.iconClass);
    const stage = {
        ...definition,
        ...raw,
        code: definition.code,
        label: raw.label || definition.label,
        ...icon,
    };
    for (const fieldName of NUMBER_FIELDS) {
        stage[fieldName] = asNumber(raw[fieldName]);
    }
    stage.progress = clampProgress(stage.progress);
    return stage;
}

export function operationalStatus(values) {
    if (values && values.has_material_shortage) {
        return { state: "shortage", label: _t("مواد ناقصة") };
    }
    const planned = asNumber(values && values.planned_qty);
    const working = asNumber(values && values.working_qty);
    const completed = asNumber(values && values.completed_qty);
    const epsilon = 0.000001;
    if (planned > epsilon && completed >= planned - epsilon) {
        return { state: "completed", label: _t("منتهي") };
    }
    if (working > epsilon || completed > epsilon) {
        return { state: "running", label: _t("جاري التنفيذ") };
    }
    return { state: "not-started", label: _t("لم يبدأ") };
}

// One display badge per product. Keep the operational quantities intact:
// working already includes pieces awaiting quality; partial completion is
// not a completed product while there are still pieces to start or finish.
export function productQuantityStatus(values) {
    const planned = asNumber(values && values.planned_qty);
    const working = asNumber(values && values.working_qty);
    const completed = asNumber(values && values.completed_qty);
    const remaining = asNumber(values && values.remaining_qty);
    const epsilon = 0.000001;
    if (planned > epsilon && completed >= planned - epsilon) {
        return { state: "completed", label: _t("مكتمل"), quantity: completed };
    }
    if (working > epsilon) {
        return { state: "working", label: _t("قيد التشغيل"), quantity: working };
    }
    return { state: "remaining", label: _t("متبقي"), quantity: remaining };
}

function normalizeProductLine(rawLine) {
    const line = rawLine && typeof rawLine === "object" ? { ...rawLine } : {};
    line.production_line_id = asNumber(line.production_line_id);
    line.production_line_ids = (Array.isArray(line.production_line_ids)
        ? line.production_line_ids
        : [line.production_line_id]
    ).map(asNumber).filter((lineId) => lineId > 0);
    line.startable_production_line_ids = (
        Array.isArray(line.startable_production_line_ids)
            ? line.startable_production_line_ids
            : []
    ).map(asNumber).filter((lineId) => lineId > 0);
    line.can_start_stage = Boolean(
        line.can_start_stage && line.startable_production_line_ids.length
    );
    line.bom_id = asNumber(line.bom_id);
    line.can_open_bom = Boolean(
        line.production_line_id > 0 && line.bom_id > 0
    );
    for (const fieldName of ORDER_QUANTITY_FIELDS) {
        line[fieldName] = asNumber(line[fieldName]);
    }
    const status = operationalStatus(line);
    line.operational_state = status.state;
    line.operational_state_label = status.label;
    line.quantity_status = productQuantityStatus(line);
    line.fabric_items = normalizeMaterialItems(line.fabric_items);
    line.takawe_items = normalizeMaterialItems(line.takawe_items);
    line.fabric_summary = String(line.fabric_summary || "").trim();
    line.takawe_summary = String(line.takawe_summary || "").trim();
    line.takawe_piece_size = String(line.takawe_piece_size || "").trim();
    line.notes = String(line.notes || "").trim();
    return line;
}

function normalizeMaterialItems(rawItems) {
    if (!Array.isArray(rawItems)) {
        return [];
    }
    return rawItems
        .map((rawItem, index) => {
            const item = rawItem && typeof rawItem === "object"
                ? { ...rawItem }
                : { label: rawItem };
            const rawProduct = item.product && typeof item.product === "object"
                ? item.product
                : {};
            const label = String(
                item.label ||
                item.product_name ||
                item.name ||
                item.display_name ||
                rawProduct.name ||
                ""
            ).trim();
            const quantityLabel = String(
                item.quantity_label ||
                item.qty_label ||
                (
                    item.qty === undefined || item.qty === null
                        ? ""
                        : `${MATERIAL_QUANTITY_FORMATTER.format(asNumber(item.qty))}${
                            item.uom ? ` ${item.uom}` : ""
                        }`
                )
            ).trim();
            const pieceSize = String(item.piece_size || "").trim();
            return {
                ...item,
                key: String(`${item.id || item.product_id || label}-${index}`),
                label,
                quantityLabel,
                pieceSize,
            };
        })
        .filter((item) => item.label || item.quantityLabel);
}

// Display ordering only: never merge batches or alter their action tokens.
export function groupStageRows(rows, productOf = (row) => row.product || row) {
    const rank = (name) => {
        const value = String(name || "").normalize("NFKC").toLowerCase()
            .replace(/[\u064B-\u065F\u0670\u0640]/g, "").replace(/[أإآ]/g, "ا")
            .replace(/ة/g, "ه").replace(/ى/g, "ي");
        if (/(?:كنب|sofa|couch)/.test(value) && /(?:كبير|large|big|3[ -]?seater|three[ -]?seater)/.test(value)) return 0;
        if (/(?:فوتي|armchair|fauteuil)/.test(value)) return 2;
        return 1;
    };
    const modelKey = (row) => {
        const product = productOf(row);
        return product.model_id ? `id:${product.model_id}` : `name:${product.model_name || ""}`;
    };
    const sorted = [...rows].sort((a, b) => {
        const left = productOf(a), right = productOf(b);
        return String(left.model_name || "").localeCompare(String(right.model_name || ""), "ar")
            || modelKey(a).localeCompare(modelKey(b), "ar", {numeric: true})
            || rank(left.product_name) - rank(right.product_name)
            || String(left.product_name || "").localeCompare(String(right.product_name || ""), "ar", {numeric: true});
    });
    return sorted.map((row, index) => ({...row,
        modelStart: index > 0 && modelKey(row) !== modelKey(sorted[index - 1]),
    }));
}

function normalizeProductBatch(rawBatch) {
    const source = rawBatch && typeof rawBatch === "object" ? { ...rawBatch } : {};
    const rawProduct = source.product && typeof source.product === "object"
        ? source.product
        : {};
    const rawModel = source.model && typeof source.model === "object"
        ? source.model
        : {};
    const rawBom = source.bom && typeof source.bom === "object"
        ? source.bom
        : {};
    const rawUom = source.uom && typeof source.uom === "object"
        ? source.uom
        : {};
    const productSource = {
        ...source,
        ...rawProduct,
        product_id: rawProduct.id || source.product_id,
        product_name: rawProduct.name || source.product_name,
        model_id: rawModel.id || source.model_id,
        model_name: rawModel.name || source.model_name,
        bom_id: rawBom.id || source.bom_id,
        bom_name: rawBom.name || source.bom_name,
        uom_id: rawUom.id || source.uom_id,
        uom: rawUom.name || (typeof source.uom === "string" ? source.uom : ""),
    };
    const product = normalizeProductLine(productSource);
    const batchToken = String(source.batch_token || "").trim();
    const state = String(source.state || "unrequested").trim() || "unrequested";
    const plannedQty = asNumber(source.planned_qty);
    const displayQty = asNumber(
        source.display_qty === undefined ? plannedQty : source.display_qty
    );
    const operationalState = state === "done"
        ? "completed"
        : state === "in_progress"
          ? "working"
          : state === "cancelled"
            ? "cancelled"
            : "remaining";
    const operationalStateLabels = {
        remaining: _t("متبقي"),
        working: _t("قيد التشغيل"),
        completed: _t("مكتمل"),
        cancelled: _t("ملغي"),
    };
    const identityKey = source.identity_key || productIdentityKey(product);
    return {
        ...source,
        batch_token: batchToken,
        identity_key: identityKey,
        product,
        planned_qty: plannedQty,
        display_qty: displayQty,
        state,
        stateClass: operationalState,
        state_label: operationalStateLabels[operationalState],
        can_request_materials: Boolean(
            source.can_request ?? source.can_request_materials
        ),
        can_receive_materials: Boolean(
            source.can_receive ?? source.can_receive_materials
        ),
        can_start: Boolean(source.can_start),
        can_finish: Boolean(source.can_finish),
        can_open_bom: Boolean(
            source.can_open_bom ?? rawBom.id ?? source.bom_id
        ),
        started_at: source.started_at || false,
        finished_at: source.finished_at || false,
        elapsed_seconds: Math.max(0, asNumber(source.elapsed_seconds)),
        bom_id: Number(rawBom.id || source.bom_id || product.bom_id) || false,
        toneClass: source.tone_class || productToneClass(identityKey),
    };
}

function normalizePendingHandoff(rawHandoff) {
    const source = rawHandoff && typeof rawHandoff === "object"
        ? { ...rawHandoff }
        : {};
    const productionId = Number(source.production_id) || 0;
    const handoffId = Number(source.handoff_id) || 0;
    const kind = source.kind === "stage" ? "stage" : "lane";
    return {
        ...source,
        key: String(source.key || `${kind}:${handoffId || productionId}`),
        kind,
        handoff_id: handoffId,
        production_id: productionId,
        title: String(source.title || _t("تحويل مرحلة منتظر")),
        message: String(source.message || ""),
        quality_rows: Array.isArray(source.quality_rows) ? source.quality_rows : [],
        quality_ready: source.quality_ready !== false,
    };
}

function normalizeIdentityText(value) {
    return String(value || "")
        .trim()
        .replace(/\s+/g, " ")
        .toLocaleLowerCase();
}

function productIdentityKey(product) {
    const uomId = Number(product && product.uom_id) || 0;
    return JSON.stringify([
        Number(product && product.product_id) || 0,
        Number(product && product.model_id) || 0,
        Number(product && product.bom_id) || 0,
        normalizeIdentityText(product && product.dimension_label),
        uomId || normalizeIdentityText(product && product.uom),
    ]);
}

function pushUnique(target, value) {
    const normalized = String(value || "").trim();
    if (normalized && !target.includes(normalized)) {
        target.push(normalized);
    }
}

function summarizeNames(values, visibleLimit = 2) {
    const names = Array.isArray(values) ? values.filter(Boolean) : [];
    if (!names.length) {
        return "—";
    }
    const visibleNames = names.slice(0, visibleLimit);
    const hiddenCount = Math.max(0, names.length - visibleNames.length);
    return hiddenCount
        ? `${visibleNames.join("، ")} (+${hiddenCount})`
        : visibleNames.join("، ");
}

const PRODUCT_CARD_TONES = [
    "is-tone-sage",
    "is-tone-sky",
    "is-tone-sand",
    "is-tone-lilac",
    "is-tone-rose",
];

function productToneClass(identityKey) {
    let hash = 0;
    for (const character of String(identityKey || "")) {
        hash = ((hash * 31) + character.charCodeAt(0)) >>> 0;
    }
    return PRODUCT_CARD_TONES[hash % PRODUCT_CARD_TONES.length];
}

/**
 * Build display-only product groups across production orders.
 *
 * Inventory, FIFO and stage tracking remain order/line based.  Only the KPI
 * drilldown merges exact product identities so management can read the actual
 * product totals without losing the contributing order audit trail.
 */
export function aggregateProductMetricRows(orders, metricFieldName) {
    if (!metricFieldName) {
        return [];
    }
    const groups = new Map();
    for (const order of orders || []) {
        if (!order || !order.id) {
            continue;
        }
        for (const product of order.product_lines || []) {
            const key = productIdentityKey(product);
            let group = groups.get(key);
            if (!group) {
                group = {
                    key,
                    product: { ...product },
                    orderCount: 0,
                    buyerNames: [],
                    beneficiaryNames: [],
                    contributors: [],
                    contributorByOrder: new Map(),
                };
                for (const fieldName of ORDER_QUANTITY_FIELDS) {
                    group[fieldName] = 0;
                }
                groups.set(key, group);
            }
            for (const fieldName of ORDER_QUANTITY_FIELDS) {
                group[fieldName] += asNumber(product[fieldName]);
            }

            let contributor = group.contributorByOrder.get(order.id);
            if (!contributor) {
                contributor = {
                    order,
                    product: { ...product },
                    buyerNames: [],
                    beneficiaryNames: [],
                    kitNames: [],
                    routeSummaries: [],
                };
                for (const fieldName of ORDER_QUANTITY_FIELDS) {
                    contributor[fieldName] = 0;
                }
                group.contributorByOrder.set(order.id, contributor);
                group.contributors.push(contributor);
            }
            for (const fieldName of ORDER_QUANTITY_FIELDS) {
                contributor[fieldName] += asNumber(product[fieldName]);
            }
            pushUnique(contributor.buyerNames, product.buyer_name);
            pushUnique(contributor.beneficiaryNames, product.beneficiary_name);
            pushUnique(contributor.kitNames, product.kit_name);
            pushUnique(contributor.routeSummaries, product.route_summary);
            pushUnique(group.buyerNames, product.buyer_name);
            pushUnique(group.beneficiaryNames, product.beneficiary_name);
        }
    }

    return [...groups.values()]
        .map((group) => {
            group.orderCount = group.contributors.length;
            group.contribution = asNumber(group[metricFieldName]);
            group.buyerSummary = summarizeNames(group.buyerNames);
            group.beneficiarySummary = summarizeNames(group.beneficiaryNames);
            group.toneClass = productToneClass(group.key);
            delete group.contributorByOrder;
            return group;
        })
        .filter((group) => group.contribution > 0)
        .sort((left, right) => {
            const leftLabel = [
                left.product.product_name,
                left.product.model_name,
                left.product.dimension_label,
            ].map(normalizeIdentityText).join("|");
            const rightLabel = [
                right.product.product_name,
                right.product.model_name,
                right.product.dimension_label,
            ].map(normalizeIdentityText).join("|");
            return leftLabel.localeCompare(rightLabel);
        });
}

function normalizeOrder(rawOrder) {
    const order = rawOrder && typeof rawOrder === "object" ? { ...rawOrder } : {};
    for (const fieldName of ORDER_QUANTITY_FIELDS) {
        order[fieldName] = asNumber(order[fieldName]);
    }
    order.progress = clampProgress(order.progress);
    order.product_lines = Array.isArray(order.product_lines)
        ? order.product_lines.map(normalizeProductLine)
        : [];
    order.worker_names = Array.isArray(order.worker_names)
        ? order.worker_names.filter(Boolean)
        : [];
    const status = operationalStatus(order);
    order.operational_state = status.state;
    order.operational_state_label = status.label;
    order.has_order_image = Boolean(order.has_order_image && order.order_image_url);
    order.order_image_url = String(order.order_image_url || "").trim();
    order.order_image_preview_url = String(
        order.order_image_preview_url || order.order_image_url || ""
    ).trim();
    order.can_request_materials = Boolean(order.can_request_materials);
    order.can_start_stage = Boolean(order.can_start_stage);
    order.can_finish_stage = Boolean(order.can_finish_stage);
    order.can_receive_materials = Boolean(order.can_receive_materials);
    order.can_open_material_request = Boolean(order.can_open_material_request);
    return order;
}

function normalizePayload(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const rawStages = Array.isArray(source.stages) ? source.stages : [];
    const rawStageByCode = Object.fromEntries(
        rawStages.filter((stage) => stage && stage.code).map((stage) => [stage.code, stage])
    );
    const allowedStageCodes = Array.isArray(source.allowed_stage_codes)
        ? new Set(source.allowed_stage_codes)
        : false;
    const definitions = allowedStageCodes
        ? STAGE_DEFINITIONS.filter((definition) => allowedStageCodes.has(definition.code))
        : STAGE_DEFINITIONS;
    const stages = definitions.map((definition) =>
        normalizeStage(rawStageByCode[definition.code], definition)
    );
    const orders = Array.isArray(source.orders)
        ? source.orders.map(normalizeOrder)
        : [];
    const productBatches = Array.isArray(source.product_batches)
        ? source.product_batches.map(normalizeProductBatch).filter(
            (batch) => batch.batch_token
        )
        : [];
    const pendingHandoffs = Array.isArray(source.pending_handoffs)
        ? source.pending_handoffs.map(normalizePendingHandoff).filter(
            (handoff) => handoff.production_id > 0 && handoff.key
        )
        : [];
    const orderIds = [
        ...new Set(
            orders
                .map((order) => Number(order && order.id))
                .filter((id) => Number.isInteger(id) && id > 0)
        ),
    ];
    const requestedStage = source.selected_stage;
    const selectedStage = stages.some((stage) => stage.code === requestedStage)
        ? requestedStage
        : stages[0]?.code || "";
    return {
        stages,
        orders,
        productBatches,
        pendingHandoffs,
        orderIds,
        selectedStage,
        supervisorMode: Boolean(source.supervisor_mode),
        batchSupervisorMode: Boolean(source.batch_supervisor_mode),
        orderSupervisorMode: Boolean(source.order_supervisor_mode),
    };
}

/**
 * Full-page factory-stage analytics client action.
 *
 * The page deliberately owns a separate payload from the order Kanban. Browsing
 * a stage therefore never filters, regroups or resequences the production cards
 * on the main dashboard.
 */
export class FurnitureMrpStageDashboardPage extends Component {
    static template = "furniture_mrp.StageDashboardPage";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.actionService = useService("action");
        this.context = this.props.action.context || {};
        this.permissions = useState({
            canCreateProduction: false,
            canManageMaterials: false,
            canCompleteProducts: false,
            isManager: false,
            isSupervisor: false,
            isLimitedSupervisor: false,
        });
        this.stageDashboard = useState({
            loading: true,
            error: "",
            hasPayload: false,
            selectedStageCode: STAGE_DEFINITIONS[0].code,
            pendingStageCode: "",
            stages: STAGE_DEFINITIONS.map((stage) => normalizeStage({}, stage)),
            orders: [],
            productBatches: [],
            pendingHandoffs: [],
            orderIds: [],
            selectedMetricKey: "orders",
            supervisorMode: false,
            batchSupervisorMode: false,
            orderSupervisorMode: false,
            busyBatchToken: "",
            busyHandoffKey: "",
            busyOrderActionKey: "",
            selectedOrderIds: [],
            selectedBatchTokens: [],
            imagePreviewUrl: "",
            imagePreviewTitle: "",
            clockTick: Date.now(),
            dateFrom: "",
            dateTo: "",
            appliedDateFrom: "",
            appliedDateTo: "",
        });
        this._stageDashboardRequest = 0;
        this._clockInterval = false;
        this._handoffRefreshTimer = null;
        this._handoffRefreshDisposed = false;
        useBus(this.env.bus, HANDOFF_ACCEPTED_EVENT, () => this._scheduleHandoffRefresh());
        this.numberFormatter = new Intl.NumberFormat(undefined, {
            maximumFractionDigits: 3,
        });
        onWillStart(async () => {
            const [
                canCreateProduction,
                isManager,
                isSupervisor,
                isPrimingSupervisor,
                isCarpentrySupervisor,
                isBasesSupervisor,
                isFinishingSupervisor,
                canCompleteProducts,
            ] = await Promise.all([
                user.checkAccessRight("furniture.mrp.production", "create"),
                user.hasGroup("furniture_mrp.group_furniture_mrp_manager"),
                user.hasGroup("furniture_mrp.group_furniture_mrp_supervisor"),
                user.hasGroup("furniture_mrp.group_furniture_mrp_supervisor_priming"),
                user.hasGroup("furniture_mrp.group_furniture_mrp_supervisor_carpentry"),
                user.hasGroup("furniture_mrp.group_furniture_mrp_supervisor_bases"),
                user.hasGroup("furniture_mrp.group_furniture_mrp_supervisor_finishing"),
                user.hasGroup("furniture_mrp.group_furniture_mrp_completion_operator"),
            ]);
            this.permissions.isManager = Boolean(isManager);
            this.permissions.isSupervisor = Boolean(isSupervisor && !isManager);
            this.permissions.isLimitedSupervisor = Boolean(
                !isManager && isSupervisor && (
                    isPrimingSupervisor || isCarpentrySupervisor ||
                    isBasesSupervisor || isFinishingSupervisor
                )
            );
            this.permissions.canCreateProduction = Boolean(
                canCreateProduction && !this.permissions.isLimitedSupervisor
            );
            this.permissions.canManageMaterials = Boolean(
                (isManager || isSupervisor) && !this.permissions.isLimitedSupervisor
            );
            this.permissions.canCompleteProducts = Boolean(canCompleteProducts);
            await this._fetchStageDashboard(false);
        });
        this._clockInterval = setInterval(() => {
            this.stageDashboard.clockTick = Date.now();
        }, 1000);
        onWillUnmount(() => {
            this._handoffRefreshDisposed = true;
            clearTimeout(this._handoffRefreshTimer);
            this._handoffRefreshTimer = null;
            if (this._clockInterval) {
                clearInterval(this._clockInterval);
                this._clockInterval = false;
            }
        });
    }

    get stageOptions() {
        return this.stageDashboard.stages;
    }

    get selectedStageCode() {
        return this.stageDashboard.selectedStageCode;
    }

    get canSendProductionWarning() {
        return Boolean(
            this.permissions.isSupervisor &&
            PRODUCTION_WARNING_SOURCE_STAGE_CODES.has(this.selectedStageCode)
        );
    }

    get showOrderTextileDetails() {
        return ["tailoring", "upholstery", "packaging"].includes(
            this.selectedStageCode
        );
    }

    get selectedStage() {
        return (
            this.stageOptions.find((stage) => stage.code === this.selectedStageCode) ||
            this.stageOptions[0]
        );
    }

    get pendingHandoffs() {
        return this.stageDashboard.pendingHandoffs;
    }

    get isBatchSupervisorMode() {
        if (!this.stageDashboard.hasPayload) {
            return this.permissions.isLimitedSupervisor;
        }
        return this.stageDashboard.batchSupervisorMode;
    }

    get isOrderWorkflowSupervisorMode() {
        return Boolean(
            this.stageDashboard.hasPayload &&
            this.stageDashboard.orderSupervisorMode &&
            ORDER_SUPERVISOR_STAGE_CODES.has(this.selectedStageCode)
        );
    }

    get orderSupervisorOrders() {
        if (!this.isOrderWorkflowSupervisorMode) {
            return [];
        }
        return groupStageRows(this.stageDashboard.orders.map((order) => ({
            ...order,
            product_lines: groupStageRows(order.product_lines || []),
            toneClass: productToneClass(`order-supervisor-${order.id}`),
        })), (order) => ({...order.product_lines?.[0], model_id: order.model_id, model_name: order.model_name}));
    }

    get selectedOrderSupervisorOrders() {
        const selectedIds = new Set(
            this.stageDashboard.selectedOrderIds.map((id) => Number(id))
        );
        if (!selectedIds.size) {
            return this.orderSupervisorOrders;
        }
        return this.orderSupervisorOrders.filter((order) =>
            selectedIds.has(Number(order.id))
        );
    }

    get orderSupervisorSummaryMetrics() {
        const orders = this.selectedOrderSupervisorOrders;
        return [
            {
                key: "orders",
                label: _t("أوامر المرحلة"),
                value: orders.length,
                icon: "fa fa-list-ul",
                tone: "orders",
            },
            {
                key: "completed",
                label: _t("مكتمل"),
                value: orders.reduce(
                    (total, order) => total + asNumber(order.completed_qty),
                    0
                ),
                icon: "fa fa-check-circle",
                tone: "completed",
            },
            {
                key: "remaining",
                label: _t("متبقي"),
                value: orders.reduce(
                    (total, order) => total + asNumber(order.remaining_qty),
                    0
                ),
                icon: "fa fa-hourglass-half",
                tone: "remaining",
            },
        ];
    }

    get hasOrderSupervisorSelection() {
        return this.stageDashboard.selectedOrderIds.length > 0;
    }

    get hasDateFilter() {
        return Boolean(
            this.stageDashboard.appliedDateFrom ||
            this.stageDashboard.appliedDateTo
        );
    }

    get summaryMetrics() {
        const stage = this.selectedStage;
        return [
            {
                key: "orders",
                fieldName: false,
                label: "أوامر المرحلة",
                value: stage.order_count,
                icon: "fa fa-list-ul",
                tone: "orders",
            },
            {
                key: "planned",
                fieldName: "planned_qty",
                label: "الكمية المخططة",
                value: stage.planned_qty,
                icon: "fa fa-calendar-check-o",
                tone: "planned",
            },
            {
                key: "working",
                fieldName: "working_qty",
                label: "قيد التشغيل",
                value: stage.working_qty,
                icon: "fa fa-cogs",
                tone: "working",
            },
            {
                key: "completed",
                fieldName: "completed_qty",
                label: "مكتمل",
                value: stage.completed_qty,
                icon: "fa fa-check-circle",
                tone: "completed",
            },
            {
                key: "remaining",
                fieldName: "remaining_qty",
                label: "متبقي",
                value: stage.remaining_qty,
                icon: "fa fa-hourglass-half",
                tone: "remaining",
            },
        ];
    }

    get selectedMetric() {
        return (
            this.summaryMetrics.find(
                (metric) => metric.key === this.stageDashboard.selectedMetricKey
            ) || false
        );
    }

    get isOrderMetric() {
        return Boolean(this.selectedMetric && !this.selectedMetric.fieldName);
    }

    get metricProductRows() {
        const metric = this.selectedMetric;
        if (!metric || !metric.fieldName) {
            return [];
        }
        return groupStageRows(aggregateProductMetricRows(
            this.stageDashboard.orders,
            metric.fieldName
        ).map((group) => ({
            ...group,
            contributionLabel: this.formatProductQuantity(
                group.contribution,
                group.product
            ),
            contributors: group.contributors.map((contributor) => ({
                ...contributor,
                contribution: asNumber(contributor[metric.fieldName]),
                contributionLabel: this.formatProductQuantity(
                    contributor[metric.fieldName],
                    group.product
                ),
            })),
        })));
    }

    get supervisorProductBatches() {
        return groupStageRows(this.stageDashboard.productBatches.map((batch) => ({
            ...batch,
            quantityLabel: this.formatProductQuantity(
                batch.display_qty,
                batch.product
            ),
        })));
    }

    get requestableSupervisorProductBatches() {
        return this.supervisorProductBatches.filter(
            (batch) => batch.can_request_materials
        );
    }

    get selectedRequestMaterialBatches() {
        const selectedTokens = new Set(this.stageDashboard.selectedBatchTokens);
        return this.requestableSupervisorProductBatches.filter((batch) =>
            selectedTokens.has(String(batch.batch_token || ""))
        );
    }

    get hasSelectedRequestMaterialBatches() {
        return this.selectedRequestMaterialBatches.length > 0;
    }

    get metricDetailRows() {
        const metric = this.selectedMetric;
        if (!metric) {
            return [];
        }
        return this.stageDashboard.orders
            .map((order) => {
                const contribution = metric.fieldName
                    ? asNumber(order && order[metric.fieldName])
                    : 1;
                const currentQty = asNumber(order && order.planned_qty);
                const workingQty = asNumber(order && order.working_qty);
                const notWorkingQty = asNumber(order && order.remaining_qty);
                return {
                    order,
                    toneClass: productToneClass(`production-order-${order && order.id}`),
                    contribution,
                    contributionLabel: metric.fieldName
                        ? this._formatOrderQuantity(contribution, order)
                        : _t("أمر واحد"),
                    currentQty,
                    workingQty,
                    notWorkingQty,
                    currentLabel: this._formatOrderQuantity(currentQty, order),
                    workingLabel: this._formatOrderQuantity(workingQty, order),
                    notWorkingLabel: this._formatOrderQuantity(notWorkingQty, order),
                };
            })
            .filter((row) => row.order && row.order.id && row.contribution > 0);
    }

    get metricWorkingBreakdown() {
        if (!this.selectedMetric || this.selectedMetric.key !== "working") {
            return false;
        }
        const rows = this.metricProductRows;
        if (!rows.length) {
            return false;
        }
        const totals = rows.reduce(
            (result, row) => ({
                currentQty: result.currentQty + asNumber(row.planned_qty),
                workingQty: result.workingQty + asNumber(row.working_qty),
                notWorkingQty: result.notWorkingQty + asNumber(row.remaining_qty),
            }),
            { currentQty: 0, workingQty: 0, notWorkingQty: 0 }
        );
        return {
            currentLabel: this._formatProductRowsQuantity(totals.currentQty, rows),
            workingLabel: this._formatProductRowsQuantity(totals.workingQty, rows),
            notWorkingLabel: this._formatProductRowsQuantity(totals.notWorkingQty, rows),
        };
    }

    get metricDetailTotalLabel() {
        const metric = this.selectedMetric;
        if (!metric) {
            return "";
        }
        if (!metric.fieldName) {
            return `${this.formatNumber(metric.value)} ${_t("أمر")}`;
        }
        const units = [
            ...new Set(
                this.metricProductRows
                    .map((row) => row.product && row.product.uom)
                    .filter(Boolean)
            ),
        ];
        const unitLabel = units.length === 1 ? units[0] : _t("وحدات متعددة");
        return `${this.formatNumber(metric.value)} ${unitLabel}`;
    }

    get progressSegments() {
        const stage = this.selectedStage;
        const workingOutsideQuality = Math.max(
            0,
            asNumber(stage.working_qty) - asNumber(stage.quality_qty)
        );
        const rawSegments = [
            {
                key: "completed",
                label: "مكتمل",
                value: Math.max(0, stage.completed_qty),
                tone: "completed",
            },
            {
                key: "quality",
                label: "في الجودة",
                value: Math.max(0, stage.quality_qty),
                tone: "quality",
            },
            {
                key: "working",
                label: "قيد التشغيل",
                value: workingOutsideQuality,
                tone: "working",
            },
            {
                key: "not_started",
                label: "لم يبدأ",
                value: Math.max(0, stage.not_started_qty),
                tone: "not-started",
            },
        ];
        const segmentTotal = rawSegments.reduce((total, segment) => total + segment.value, 0);
        const total = Math.max(stage.planned_qty, segmentTotal, 1);
        return rawSegments.map((segment) => ({
            ...segment,
            percentage: (segment.value / total) * 100,
            style: `width: ${Math.max(0, Math.min(100, (segment.value / total) * 100))}%;`,
        }));
    }

    stageProgressStyle(stage) {
        return `--furniture-stage-progress: ${clampProgress(stage.progress)}%;`;
    }

    formatNumber(value) {
        return this.numberFormatter.format(asNumber(value));
    }

    formatProductQuantity(value, productLine) {
        const rawUnitLabel = productLine && productLine.uom
            ? String(productLine.uom).trim()
            : "";
        const normalizedUnitLabel = rawUnitLabel.toLocaleLowerCase();
        const unitLabel = ["unit", "units", "unit(s)"].includes(normalizedUnitLabel)
            ? _t("وحدات")
            : rawUnitLabel || _t("وحدات");
        return `${this.formatNumber(value)} ${unitLabel}`;
    }

    formatDateTime(value) {
        if (!value) {
            return _t("غير مسجل");
        }
        const date = parseServerDate(value);
        if (!date) {
            return String(value);
        }
        return new Intl.DateTimeFormat(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
        }).format(date);
    }

    formatBatchElapsed(batch) {
        // Reading the reactive tick keeps every visible running timer live.
        void this.stageDashboard.clockTick;
        const start = parseServerDate(batch && batch.started_at);
        const finish = parseServerDate(batch && batch.finished_at);
        const totalSeconds = start
            ? Math.max(
                0,
                Math.floor(((finish ? finish.getTime() : Date.now()) - start.getTime()) / 1000)
            )
            : Math.max(0, Math.floor(asNumber(batch && batch.elapsed_seconds)));
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        return [hours, minutes, seconds]
            .map((part) => String(part).padStart(2, "0"))
            .join(":");
    }

    isProductBatchBusy(batchToken) {
        return Boolean(batchToken) && (
            this.stageDashboard.busyBatchToken === String(batchToken) ||
            (
                this.stageDashboard.busyBatchToken === "__bulk_material_request__" &&
                this.isProductBatchSelected(batchToken)
            )
        );
    }

    isProductBatchSelected(batchToken) {
        const normalizedToken = String(batchToken || "").trim();
        return Boolean(normalizedToken) && this.stageDashboard.selectedBatchTokens.includes(
            normalizedToken
        );
    }

    toggleProductBatchSelection(batch, event) {
        event?.stopPropagation?.();
        if (!batch?.can_request_materials || this.stageDashboard.busyBatchToken) {
            return;
        }
        const token = String(batch.batch_token || "").trim();
        if (!token) {
            return;
        }
        const selected = new Set(this.stageDashboard.selectedBatchTokens);
        if (selected.has(token)) {
            selected.delete(token);
        } else {
            selected.add(token);
        }
        this.stageDashboard.selectedBatchTokens = [...selected];
    }

    isSupervisorOrderSelected(orderId) {
        const normalizedId = Number(orderId);
        return this.stageDashboard.selectedOrderIds.some(
            (id) => Number(id) === normalizedId
        );
    }

    toggleSupervisorOrderSelection(orderId) {
        const normalizedId = Number(orderId);
        if (!Number.isInteger(normalizedId) || normalizedId <= 0) {
            return;
        }
        const selectedIds = new Set(
            this.stageDashboard.selectedOrderIds.map((id) => Number(id))
        );
        if (selectedIds.has(normalizedId)) {
            selectedIds.delete(normalizedId);
        } else {
            selectedIds.add(normalizedId);
        }
        this.stageDashboard.selectedOrderIds = [...selectedIds];
    }

    clearSupervisorOrderSelection() {
        this.stageDashboard.selectedOrderIds = [];
    }

    isOrderSupervisorActionBusy(orderId, actionName = "") {
        const prefix = actionName ? `${actionName}:` : "";
        return this.stageDashboard.busyOrderActionKey === `${prefix}${Number(orderId)}`;
    }

    openOrderImage(order) {
        if (!order || !order.has_order_image || !order.order_image_url) {
            return;
        }
        this.stageDashboard.imagePreviewUrl =
            order.order_image_preview_url || order.order_image_url;
        this.stageDashboard.imagePreviewTitle = order.name || _t("صورة أمر الإنتاج");
    }

    closeOrderImage() {
        this.stageDashboard.imagePreviewUrl = "";
        this.stageDashboard.imagePreviewTitle = "";
    }

    _formatOrderQuantity(value, order) {
        const units = Array.isArray(order && order.uoms)
            ? order.uoms.filter(Boolean)
            : [];
        const unitLabel = order && order.has_mixed_uom
            ? _t("وحدات متعددة")
            : order && order.uom
              ? order.uom
              : units.length === 1
                ? units[0]
                : _t("وحدة");
        return `${this.formatNumber(value)} ${unitLabel}`;
    }

    _formatMetricRowsQuantity(value, rows) {
        const unitLabels = new Set();
        let hasMixedUom = false;
        for (const row of rows) {
            const order = row && row.order;
            hasMixedUom ||= Boolean(order && order.has_mixed_uom);
            const orderUnits = Array.isArray(order && order.uoms)
                ? order.uoms.filter(Boolean)
                : [];
            if (order && order.uom) {
                unitLabels.add(order.uom);
            }
            for (const unit of orderUnits) {
                unitLabels.add(unit);
            }
        }
        const unitLabel = !hasMixedUom && unitLabels.size === 1
            ? [...unitLabels][0]
            : _t("وحدات متعددة");
        return `${this.formatNumber(value)} ${unitLabel}`;
    }

    _formatProductRowsQuantity(value, rows) {
        const unitLabels = new Set(
            rows
                .map((row) => row && row.product && row.product.uom)
                .filter(Boolean)
        );
        const unitLabel = unitLabels.size === 1
            ? [...unitLabels][0]
            : _t("وحدات متعددة");
        return `${this.formatNumber(value)} ${unitLabel}`;
    }

    toggleMetricDetails(metricKey) {
        if (!this.summaryMetrics.some((metric) => metric.key === metricKey)) {
            return;
        }
        this.stageDashboard.selectedMetricKey =
            this.stageDashboard.selectedMetricKey === metricKey ? "" : metricKey;
    }

    closeMetricDetails() {
        this.stageDashboard.selectedMetricKey = "";
    }

    async openProductionOrder(orderId) {
        const productionId = Number(orderId);
        if (!Number.isInteger(productionId) || productionId <= 0) {
            return;
        }
        const order = this.stageDashboard.orders.find(
            (candidate) => Number(candidate && candidate.id) === productionId
        );
        this.dialog.add(FormViewDialog, {
            resModel: "furniture.mrp.production",
            resId: productionId,
            mode: "readonly",
            preventCreate: true,
            title: (order && order.name) || _t("أمر الإنتاج"),
            size: "xl",
            context: {
                ...this.context,
                active_model: "furniture.mrp.production",
                active_id: productionId,
                furniture_dashboard_stage: this.selectedStageCode,
            },
        });
    }

    async openProductBatchBom(batch) {
        if (!batch || !batch.batch_token || !batch.can_open_bom) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () => {
            const result = await this._callProductBatchAction(
                "action_open_stage_dashboard_product_batch_bom",
                batch
            );
            const action = this._productBatchActionFromResult(result);
            if (action) {
                await this.actionService.doAction(action);
            }
        }, { reload: false });
    }

    async openProductBatchWarning(batch) {
        if (!batch?.batch_token) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () => {
            const action = await this._callProductBatchAction(
                "action_open_stage_dashboard_product_warning",
                batch
            );
            if (action) {
                await this.actionService.doAction(action);
            }
        }, { reload: false });
    }

    async openOrderProductWarning(order, product) {
        const productionLineId = Number(product?.production_line_id);
        if (!order?.id || !productionLineId) {
            return;
        }
        await this._runOrderSupervisorAction(
            order,
            `warning-${productionLineId}`,
            async () => this.orm.call(
                "furniture.mrp.production",
                "action_open_stage_dashboard_order_product_warning",
                [[Number(order.id)]],
                {
                    production_line_id: productionLineId,
                    stage_code: this.selectedStageCode,
                    context: this.context,
                }
            ),
            { reload: false }
        );
    }

    _showProductionWarning(payload) {
        if (!payload?.production_quality_warning) {
            return;
        }
        this.dialog.add(AlertDialog, {
            title: payload.title || _t("تحذير إنتاج"),
            body: payload.message || "",
            confirmLabel: _t("فهمت"),
            confirmClass: "btn-warning",
            contentClass: "o_furniture_production_warning_dialog",
        });
    }

    async requestProductBatchMaterials(batch) {
        if (!batch || !batch.can_request_materials) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () => {
            const result = await this._callProductBatchAction(
                "action_stage_dashboard_request_product_batch_materials",
                batch
            );
            const action = this._productBatchActionFromResult(result);
            if (action) {
                await this.actionService.doAction(action);
            }
        });
    }

    async requestSelectedProductBatchMaterials() {
        const batches = this.selectedRequestMaterialBatches;
        if (!batches.length || this.stageDashboard.busyBatchToken) {
            return;
        }
        const stageCode = this.selectedStageCode;
        const batchTokens = batches.map((batch) => String(batch.batch_token));
        this.stageDashboard.busyBatchToken = "__bulk_material_request__";
        try {
            const result = await this.orm.call(
                "furniture.mrp.production",
                "action_stage_dashboard_request_product_batches_materials",
                [],
                {
                    batch_tokens: batchTokens,
                    stage_code: stageCode,
                    context: this.context,
                }
            );
            this.stageDashboard.selectedBatchTokens = [];
            await this._reloadStage(stageCode);
            this.notification.add(
                _t("تم إرسال طلب خامات %s أصناف دفعة واحدة.", result?.requested_count || batchTokens.length),
                { type: "success" }
            );
        } catch (error) {
            this.notification.add(
                error?.data?.message || error?.message || _t("تعذر إرسال طلبات الخامات المحددة."),
                { type: "danger", sticky: true }
            );
        } finally {
            this.stageDashboard.busyBatchToken = "";
        }
    }

    async requestOrderStageMaterials(order) {
        if (
            !order ||
            (
                !order.can_request_materials &&
                !order.can_receive_materials &&
                !order.can_open_material_request
            )
        ) {
            return;
        }
        await this._runOrderSupervisorAction(order, "materials", async () =>
            this.orm.call(
                "furniture.mrp.production",
                "action_stage_dashboard_request_order_materials",
                [[Number(order.id)]],
                {
                    stage_code: this.selectedStageCode,
                    context: this.context,
                }
            )
        );
    }

    async openOrderProductBom(order, product) {
        const productionLineId = Number(product && product.production_line_id);
        if (
            !order ||
            !product ||
            !product.can_open_bom ||
            !Number.isInteger(productionLineId) ||
            productionLineId <= 0
        ) {
            return;
        }
        await this._runOrderSupervisorAction(order, `bom-${productionLineId}`, async () =>
            this.orm.call(
                "furniture.mrp.production",
                "action_open_stage_dashboard_bom",
                [[Number(order.id)]],
                {
                    production_line_id: productionLineId,
                    stage_code: this.selectedStageCode,
                    context: this.context,
                }
            ),
            { reload: false }
        );
    }

    async startOrderProductStage(order, product) {
        const productionLineIds = (
            product && Array.isArray(product.startable_production_line_ids)
                ? product.startable_production_line_ids
                : []
        ).map(Number).filter((lineId) => Number.isInteger(lineId) && lineId > 0);
        if (!order || !product || !product.can_start_stage || !productionLineIds.length) {
            return;
        }
        await this._runOrderSupervisorAction(
            order,
            `start-product-${Number(product.production_line_id)}`,
            async () => {
                const result = await this.orm.call(
                    "furniture.mrp.production",
                    "action_stage_dashboard_start_order_product",
                    [[Number(order.id)]],
                    {
                        production_line_ids: productionLineIds,
                        stage_code: this.selectedStageCode,
                        context: this.context,
                    }
                );
                this._showProductionWarning(result?.production_warning);
                return result;
            }
        );
    }

    async startOrderAllProducts(order) {
        const productionLineIds = [
            ...new Set(
                (order && Array.isArray(order.product_lines) ? order.product_lines : [])
                    .filter((product) => product && product.can_start_stage)
                    .flatMap((product) =>
                        Array.isArray(product.startable_production_line_ids)
                            ? product.startable_production_line_ids
                            : []
                    )
                    .map(Number)
                    .filter((lineId) => Number.isInteger(lineId) && lineId > 0)
            ),
        ];
        if (!order || !order.can_start_stage || !productionLineIds.length) {
            return;
        }
        await this._runOrderSupervisorAction(order, "start-all", async () =>
            this.orm.call(
                "furniture.mrp.production",
                "action_stage_dashboard_start_order_product",
                [[Number(order.id)]],
                {
                    production_line_ids: productionLineIds,
                    stage_code: this.selectedStageCode,
                    context: this.context,
                }
            )
        );
    }

    async finishOrderStage(order) {
        if (!order || !order.can_finish_stage || !order.quality_ready) {
            return;
        }
        await this._runOrderSupervisorAction(order, "finish", async () =>
            this.orm.call(
                "furniture.mrp.production",
                "action_stage_dashboard_finish_order_stage",
                [[Number(order.id)]],
                {
                    stage_code: this.selectedStageCode,
                    context: this.context,
                }
            )
        );
    }

    qualityStateLabel(state) {
        return state === "pass" ? _t("الجودة مقبولة")
            : state === "reject" ? _t("الجودة مرفوضة — أعد الفحص")
            : _t("بانتظار فحص الجودة");
    }

    async reviewOrderProductQuality(order, product, decision) {
        if (!product?.can_review_quality) {
            return;
        }
        await this._runOrderSupervisorAction(order, "quality-" + product.production_line_id, async () =>
            this.orm.call("furniture.mrp.production", "action_stage_dashboard_review_order_product_quality",
                [[Number(order.id)]], {
                    production_line_ids: product.quality_production_line_ids,
                    stage_code: this.selectedStageCode,
                    decision,
                    context: this.context,
                })
        );
    }

    async reviewProductBatchQuality(batch, decision) {
        if (!batch?.can_review_quality) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () =>
            this._callProductBatchAction("action_stage_dashboard_review_product_batch_quality", batch, { decision })
        );
    }

    async receiveProductBatchMaterials(batch) {
        if (!batch || !batch.can_receive_materials) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () => {
            const result = await this._callProductBatchAction(
                "action_stage_dashboard_receive_product_batch_materials",
                batch
            );
            const action = this._productBatchActionFromResult(result);
            if (action) {
                await this.actionService.doAction(action);
            }
        });
    }

    async startProductBatch(batch) {
        if (!batch || !batch.can_start) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () => {
            const result = await this._callProductBatchAction(
                "action_stage_dashboard_start_product_batch",
                batch
            );
            this._showProductionWarning(result?.production_warning);
            const action = this._productBatchActionFromResult(result);
            if (action) {
                await this.actionService.doAction(action);
            }
        });
    }

    async finishProductBatch(batch) {
        if (!batch || !batch.can_finish || !batch.quality_ready) {
            return;
        }
        await this._runProductBatchAction(batch.batch_token, async () => {
            const result = await this._callProductBatchAction(
                "action_stage_dashboard_finish_product_batch",
                batch
            );
            const action = this._productBatchActionFromResult(result);
            if (action) {
                await this.actionService.doAction(action);
            }
        });
    }

    async _callProductBatchAction(methodName, batch, extra = {}) {
        return this.orm.call(
            "furniture.mrp.production",
            methodName,
            [],
            {
                batch_token: batch.batch_token,
                stage_code: batch.stage_code || this.selectedStageCode,
                ...extra,
                context: this.context,
            }
        );
    }

    _productBatchActionFromResult(result) {
        if (!result || typeof result !== "object") {
            return false;
        }
        let action = false;
        if (result.action && typeof result.action === "object") {
            action = result.action;
        } else if (result.type) {
            action = result;
        }
        if (!action) {
            return false;
        }
        // Direct ORM calls bypass the web controller's action normalisation.
        // Older/custom act_window payloads may therefore contain view_mode
        // without views, while the action service always preprocesses
        // action.views.map(...).  Complete that harmless missing shape here so
        // a valid wizard never turns into an Owl/JavaScript client crash.
        if (
            action.type === "ir.actions.act_window" &&
            !Array.isArray(action.views)
        ) {
            const viewModes = String(action.view_mode || "form")
                .split(",")
                .map((mode) => mode.trim())
                .filter(Boolean);
            const rawViewId = Array.isArray(action.view_id)
                ? action.view_id[0]
                : action.view_id;
            const viewId = Number.isInteger(Number(rawViewId)) && Number(rawViewId) > 0
                ? Number(rawViewId)
                : false;
            action = {
                ...action,
                views: (viewModes.length ? viewModes : ["form"]).map(
                    (viewMode, index) => [index === 0 ? viewId : false, viewMode]
                ),
            };
        }
        return action;
    }

    async _runProductBatchAction(batchToken, callback, { reload = true } = {}) {
        const normalizedToken = String(batchToken || "").trim();
        if (!normalizedToken || this.stageDashboard.busyBatchToken) {
            return;
        }
        this.stageDashboard.busyBatchToken = normalizedToken;
        try {
            await callback();
            if (reload) {
                await this._reloadStage(this.selectedStageCode);
            }
        } finally {
            this.stageDashboard.busyBatchToken = "";
        }
    }

    async _runOrderSupervisorAction(
        order,
        actionName,
        callback,
        { reload = true } = {}
    ) {
        const orderId = Number(order && order.id);
        const actionKey = `${actionName}:${orderId}`;
        if (
            !Number.isInteger(orderId) ||
            orderId <= 0 ||
            this.stageDashboard.busyOrderActionKey
        ) {
            return;
        }
        this.stageDashboard.busyOrderActionKey = actionKey;
        try {
            const result = await callback();
            const action = this._productBatchActionFromResult(result);
            if (action) {
                await this.actionService.doAction(action);
            }
            if (reload) {
                await this._reloadStage(this.selectedStageCode, {
                    preserveOrderSelection: true,
                });
            }
        } finally {
            this.stageDashboard.busyOrderActionKey = "";
        }
    }

    async createProductionOrder() {
        if (!this.permissions.canCreateProduction) {
            return;
        }
        await this.actionService.doAction(
            "furniture_mrp.action_furniture_mrp_new_production_product_wizard"
        );
    }

    async openFutureDeliveryOrders() {
        if (!this.permissions.isManager) {
            return;
        }
        await this.actionService.doAction(
            "furniture_mrp.action_furniture_mrp_future_order"
        );
    }

    async openBatchMaterialWizard() {
        if (!this.permissions.canManageMaterials) {
            return;
        }
        await this.actionService.doAction(
            "furniture_mrp.action_furniture_mrp_advance_material_batch_wizard"
        );
    }

    async openBatchMaterialCheckWizard() {
        if (!this.permissions.canManageMaterials) {
            return;
        }
        await this.actionService.doAction(
            "furniture_mrp.action_furniture_mrp_material_check_batch_wizard"
        );
    }

    async selectStage(stageCode) {
        if (
            !this.stageOptions.some((stage) => stage.code === stageCode) ||
            this.stageDashboard.loading
        ) {
            return;
        }
        await this._reloadStage(stageCode);
    }

    async refreshDashboard() {
        if (this.stageDashboard.loading) {
            return;
        }
        await this._reloadStage(this.selectedStageCode, {
            preserveOrderSelection: this.isOrderWorkflowSupervisorMode,
        });
    }

    _scheduleHandoffRefresh() {
        if (this._handoffRefreshDisposed) {
            return;
        }
        // Coalesce the successful RPC and its websocket acknowledgement.
        clearTimeout(this._handoffRefreshTimer);
        this._handoffRefreshTimer = setTimeout(async () => {
            this._handoffRefreshTimer = null;
            if (this._handoffRefreshDisposed) {
                return;
            }
            const state = this.stageDashboard;
            if (state.loading || state.busyHandoffKey || state.busyBatchToken || state.busyOrderActionKey) {
                // Do not race a stage change or an in-progress supervisor action.
                this._scheduleHandoffRefresh();
                return;
            }
            try {
                // Reload the CURRENT stage and keep the applied dates/selected orders.
                // The normal reload also updates the planned/completed extension.
                await this._reloadStage(this.selectedStageCode, { preserveOrderSelection: true });
            } catch {
                if (!this._handoffRefreshDisposed) {
                    this.notification.add(_t("تم قبول التحويل، لكن تعذر تحديث اللوحة. اضغط تحديث."), {
                        type: "warning",
                    });
                }
            }
        }, 1000);
    }

    async reviewHandoffQuality(handoff, row, decision) {
        if (this.stageDashboard.busyHandoffKey || !handoff?.production_id || !row?.key) {
            return;
        }
        this.stageDashboard.busyHandoffKey = handoff.key;
        try {
            await this.orm.call("furniture.mrp.production", "action_review_handoff_quality",
                [[Number(handoff.production_id)]], {
                    review_key: row.key,
                    decision,
                    stage_handoff_id: handoff.kind === "stage" ? handoff.handoff_id : false,
                    context: this.context,
                });
            this.notification.add(_t("تم تسجيل قرار الجودة فقط، ولم يتم قبول التحويل أو نقل المخزون."), { type: "success" });
            await this._reloadStage(this.selectedStageCode);
        } catch (error) {
            this.notification.add(error?.data?.message || error?.message || _t("تعذر تسجيل قرار الجودة."), { type: "danger", sticky: true });
        } finally {
            this.stageDashboard.busyHandoffKey = "";
        }
    }

    async acceptPendingHandoff(handoff) {
        const productionId = Number(handoff && handoff.production_id);
        const handoffId = Number(handoff && handoff.handoff_id) || 0;
        const handoffKey = String(handoff && handoff.key || "");
        if (
            !Number.isInteger(productionId) || productionId <= 0 ||
            !handoffKey || this.stageDashboard.busyHandoffKey || !handoff.quality_ready
        ) {
            return;
        }
        this.stageDashboard.busyHandoffKey = handoffKey;
        try {
            const result = await this.orm.call(
                "furniture.mrp.production",
                "action_accept_handoff_transfer",
                [[productionId]],
                {
                    ...(handoff.kind === "stage" && handoffId
                        ? { stage_handoff_id: handoffId }
                        : {}),
                    context: this.context,
                }
            );
            // The transfer is resolved at this point. Remove its durable banner
            // immediately instead of leaving it visible until the deferred
            // dashboard refresh finishes.
            this.stageDashboard.pendingHandoffs = this.stageDashboard.pendingHandoffs.filter(
                (item) => item.key !== handoffKey
            );
            this.env.bus.trigger(HANDOFF_ACCEPTED_EVENT);
            this.notification.add(
                result?.message || _t("تم تنفيذ حركة المخزون ويمكن بدء المرحلة."),
                {
                    title: result?.title || _t("تم قبول التحويل"),
                    type: "success",
                }
            );
        } catch (error) {
            this.notification.add(
                error?.data?.message || error?.message || _t("تعذر قبول التحويل."),
                {
                    title: _t("لم يتم تنفيذ التحويل"),
                    type: "danger",
                    sticky: true,
                }
            );
        } finally {
            this.stageDashboard.busyHandoffKey = "";
        }
    }

    onDateFromChange(event) {
        this.stageDashboard.dateFrom = String(event.target.value || "");
    }

    onDateToChange(event) {
        this.stageDashboard.dateTo = String(event.target.value || "");
    }

    get visibilityDateExpired() {
        const now = new Date(this.stageDashboard.clockTick || Date.now());
        const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
        return Boolean(this.stageDashboard.savedVisibilityDate && this.stageDashboard.savedVisibilityDate <= today);
    }

    async applyDateFilter() {
        if (this.stageDashboard.loading || !this.permissions.isManager) return;
        this.stageDashboard.loading = true;
        try {
            await this.orm.call("furniture.mrp.production", "set_stage_visibility_date", [], {
                stage_code: this.selectedStageCode,
                visibility_date: this.stageDashboard.dateTo || false,
                context: this.context,
            });
            await this._fetchStageDashboard(this.selectedStageCode);
            this.notification.add(_t("تم حفظ تاريخ إظهار أوامر المرحلة."), { type: "success" });
        } finally {
            this.stageDashboard.loading = false;
        }
    }

    async _reloadStage(stageCode, { preserveOrderSelection = false } = {}) {
        this.stageDashboard.loading = true;
        this.stageDashboard.pendingStageCode = stageCode;
        this.stageDashboard.selectedMetricKey = "orders";
        if (!preserveOrderSelection) {
            this.stageDashboard.selectedOrderIds = [];
        }
        await this._fetchStageDashboard(stageCode, { finishLoading: false });
        this.stageDashboard.loading = false;
        this.stageDashboard.pendingStageCode = "";
    }

    async _fetchStageDashboard(stageCode, { finishLoading = true } = {}) {
        const requestNumber = ++this._stageDashboardRequest;
        try {
            const payload = await this.orm.call(
                "furniture.mrp.production",
                "get_stage_dashboard_data",
                [],
                {
                    stage_code: stageCode || false,

                    context: this.context,
                }
            );
            if (requestNumber !== this._stageDashboardRequest) {
                return false;
            }
            this.stageDashboard.dateTo = payload.visibility_date || "";
            this.stageDashboard.savedVisibilityDate = payload.visibility_date || "";
            const normalized = normalizePayload(payload);
            this.stageDashboard.stages = normalized.stages;
            this.stageDashboard.orders = normalized.orders;
            this.stageDashboard.productBatches = normalized.productBatches;
            this.stageDashboard.pendingHandoffs = normalized.pendingHandoffs;
            this.stageDashboard.orderIds = normalized.orderIds;
            this.stageDashboard.selectedStageCode = normalized.selectedStage;
            this.stageDashboard.supervisorMode = normalized.supervisorMode;
            this.stageDashboard.batchSupervisorMode = normalized.batchSupervisorMode;
            this.stageDashboard.orderSupervisorMode = normalized.orderSupervisorMode;
            const validOrderIds = new Set(normalized.orderIds);
            this.stageDashboard.selectedOrderIds = (
                this.stageDashboard.selectedOrderIds || []
            ).filter((id) => validOrderIds.has(Number(id)));
            const requestableBatchTokens = new Set(
                normalized.productBatches
                    .filter((batch) => batch.can_request_materials)
                    .map((batch) => String(batch.batch_token || ""))
            );
            this.stageDashboard.selectedBatchTokens = (
                this.stageDashboard.selectedBatchTokens || []
            ).filter((token) => requestableBatchTokens.has(String(token)));
            if (normalized.batchSupervisorMode) {
                this.stageDashboard.selectedMetricKey = "planned";
            }
            this.stageDashboard.hasPayload = true;
            this.stageDashboard.error = "";
            return true;
        } catch (error) {
            if (requestNumber !== this._stageDashboardRequest) {
                return false;
            }
            this.stageDashboard.error = _t("تعذر تحميل بيانات مراحل لوحة التحكم.");
            this.notification.add(this.stageDashboard.error, {
                title: _t("لوحة تحكم المصنع"),
                type: "warning",
            });
            return false;
        } finally {
            if (finishLoading && requestNumber === this._stageDashboardRequest) {
                this.stageDashboard.loading = false;
                this.stageDashboard.pendingStageCode = "";
            }
        }
    }

}

/** Keep the main dashboard native and use its header button only as navigation. */
export class FurnitureMrpStageDashboardController extends KanbanController {
    static template = "furniture_mrp.StageDashboardKanbanView";

    setup() {
        super.setup();
        this.actionService = useService("action");
    }

    async openStageDashboard() {
        await this.actionService.doAction(
            "furniture_mrp.action_furniture_mrp_stage_dashboard_page"
        );
    }
}

export const furnitureMrpStageDashboardView = {
    ...kanbanView,
    Controller: FurnitureMrpStageDashboardController,
};

registry.category("views").add(
    "furniture_mrp_stage_dashboard",
    furnitureMrpStageDashboardView
);

registry.category("actions").add(
    "furniture_mrp_stage_dashboard_page",
    FurnitureMrpStageDashboardPage
);

const MPS_ASSIGNMENT_ACTIVE_STATES = ["proposed", "approved", "in_progress"];
const MPS_ASSIGNMENT_STATE_LABELS = {
    proposed: _t("مقترح"),
    approved: _t("معتمد"),
    in_progress: _t("جاري التنفيذ"),
    done: _t("مكتمل"),
    cancelled: _t("ملغي"),
};

function relationName(value, fallback = "") {
    if (Array.isArray(value)) {
        return String(value[1] || fallback);
    }
    return String(value || fallback);
}

function relationId(value) {
    return Array.isArray(value) ? Number(value[0]) || 0 : Number(value) || 0;
}

function dateInputValue(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function currentFactoryWeekRange(referenceDate = new Date()) {
    const start = new Date(
        referenceDate.getFullYear(),
        referenceDate.getMonth(),
        referenceDate.getDate()
    );
    // Factory week is Saturday through Thursday; Friday is the weekly leave.
    start.setDate(start.getDate() - ((start.getDay() + 1) % 7));
    const end = new Date(start);
    end.setDate(end.getDate() + 5);
    return { dateFrom: dateInputValue(start), dateTo: dateInputValue(end) };
}

/**
 * Compact, worker-first MPS dashboard.
 *
 * The client action intentionally reads normal models through the ORM instead
 * of a sudo dashboard endpoint. Existing company, supervisor and worker record
 * rules therefore remain the single source of visibility for every card.
 */
export class FurnitureMrpMPSDashboardPage extends Component {
    static template = "furniture_mrp.MPSDashboardPage";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.context = this.props.action.context || {};
        this.permissions = useState({
            isManager: false,
            isSupervisor: false,
        });
        this.filters = useState({
            stage: "",
            state: "",
            dateFrom: "",
            dateTo: "",
        });
        this.dashboard = useState({
            loading: true,
            error: "",
            assignments: [],
            sourceAssignmentCount: 0,
            workers: [],
            assignmentCount: 0,
            pieceCount: 0,
            plannedHours: 0,
        });
        this._requestNumber = 0;
        this.quantityFormatter = new Intl.NumberFormat(undefined, {
            maximumFractionDigits: 0,
        });
        this.hoursFormatter = new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
        });

        onWillStart(async () => {
            const permissionsRequest = Promise.all([
                user.hasGroup("furniture_mrp.group_furniture_mrp_manager"),
                user.hasGroup("furniture_mrp.group_furniture_mrp_supervisor"),
            ])
                .then(([isManager, isSupervisor]) => {
                    this.permissions.isManager = Boolean(isManager);
                    this.permissions.isSupervisor = Boolean(
                        isSupervisor && !isManager
                    );
                })
                .catch(() => {
                    this.permissions.isManager = false;
                    this.permissions.isSupervisor = false;
                });
            await Promise.all([permissionsRequest, this.loadDashboard()]);
        });
    }

    get pageTitle() {
        if (this.permissions.isManager) {
            return _t("توزيع المصنع");
        }
        if (this.permissions.isSupervisor) {
            return _t("جدول الفريق");
        }
        return _t("جدول شغلي");
    }

    get pageSubtitle() {
        if (this.permissions.isManager) {
            return _t("كل عامل عليه ماذا، بقطع كاملة ومواعيد واضحة");
        }
        if (this.permissions.isSupervisor) {
            return _t("تكليفات الفريق الظاهرة لك حسب صلاحيات مرحلتك");
        }
        return _t("تكليفاتك أنت فقط حسب حساب العامل المرتبط بك");
    }

    get scopeLabel() {
        if (this.permissions.isManager) {
            return _t("نطاق المصنع");
        }
        if (this.permissions.isSupervisor) {
            return _t("نطاق الفريق");
        }
        return _t("تكليفاتي فقط");
    }

    get stageFilterOptions() {
        return STAGE_DEFINITIONS;
    }

    get activeFilterCount() {
        return Object.values(this.filters).filter(Boolean).length;
    }

    get hasSourceAssignments() {
        return this.dashboard.sourceAssignmentCount > 0;
    }

    get emptyTitle() {
        if (this.hasSourceAssignments && this.activeFilterCount) {
            return _t("لا توجد تكليفات تطابق الفلاتر الحالية");
        }
        if (this.permissions.isManager) {
            return _t("لا توجد تكليفات مفتوحة في توزيع المصنع الآن");
        }
        if (this.permissions.isSupervisor) {
            return _t("لا توجد تكليفات مفتوحة للفريق الظاهر لك الآن");
        }
        return _t("لا توجد تكليفات مفتوحة في جدول شغلك الآن");
    }

    get emptyHint() {
        if (this.hasSourceAssignments && this.activeFilterCount) {
            return _t("غيّر المرحلة أو الحالة أو الفترة، أو امسح الفلاتر.");
        }
        return _t("ستظهر التكليفات هنا تلقائيًا بعد اعتماد توزيع MPS.");
    }

    async loadDashboard() {
        const requestNumber = ++this._requestNumber;
        this.dashboard.loading = true;
        this.dashboard.error = "";
        try {
            const assignments = await this.orm.searchRead(
                "furniture.mrp.mps.assignment",
                [["state", "in", MPS_ASSIGNMENT_ACTIVE_STATES]],
                [
                    "employee_id",
                    "production_id",
                    "furniture_model_id",
                    "stage",
                    "operation_name",
                    "output_summary",
                    "piece_count",
                    "planned_hours",
                    "planned_start",
                    "planned_end",
                    "state",
                ],
                {
                    context: this.context,
                    order: "employee_id, planned_start, id",
                }
            );
            const assignmentIds = assignments.map((assignment) => assignment.id);
            const outputs = assignmentIds.length
                ? await this.orm.searchRead(
                    "furniture.mrp.mps.assignment.output",
                    [["assignment_id", "in", assignmentIds]],
                    [
                        "assignment_id",
                        "product_id",
                        "quantity",
                        "piece_duration_hours",
                        "planned_hours",
                    ],
                    {
                        context: this.context,
                        order: "assignment_id, product_id, id",
                    }
                )
                : [];
            if (requestNumber !== this._requestNumber) {
                return;
            }
            this._applyAssignments(assignments, outputs);
        } catch (error) {
            if (requestNumber !== this._requestNumber) {
                return;
            }
            this.dashboard.error = _t("تعذر تحميل خطة العمال الآن.");
            this.notification.add(this.dashboard.error, {
                title: _t("تخطيط المراحل والعمال"),
                type: "warning",
            });
        } finally {
            if (requestNumber === this._requestNumber) {
                this.dashboard.loading = false;
            }
        }
    }

    _applyAssignments(rawAssignments, rawOutputs) {
        const outputsByAssignment = new Map();
        for (const rawOutput of rawOutputs) {
            const assignmentId = relationId(rawOutput.assignment_id);
            const output = {
                id: rawOutput.id,
                productName: relationName(rawOutput.product_id, _t("صنف")),
                quantity: Math.max(0, Math.round(asNumber(rawOutput.quantity))),
                pieceHours: Math.max(0, asNumber(rawOutput.piece_duration_hours)),
                plannedHours: Math.max(0, asNumber(rawOutput.planned_hours)),
            };
            if (!outputsByAssignment.has(assignmentId)) {
                outputsByAssignment.set(assignmentId, []);
            }
            outputsByAssignment.get(assignmentId).push(output);
        }

        const assignments = [];
        for (const rawAssignment of rawAssignments) {
            const outputLines = outputsByAssignment.get(rawAssignment.id) || [];
            const outputPieceCount = outputLines.reduce(
                (total, output) => total + output.quantity,
                0
            );
            const assignmentPieceCount = Math.max(
                0,
                Math.round(asNumber(rawAssignment.piece_count) || outputPieceCount)
            );
            const assignmentHours = Math.max(0, asNumber(rawAssignment.planned_hours));
            const stage = String(rawAssignment.stage || "");
            const assignment = {
                id: rawAssignment.id,
                employeeId: relationId(rawAssignment.employee_id),
                employeeName: relationName(
                    rawAssignment.employee_id,
                    _t("عامل غير محدد")
                ),
                productionName: relationName(rawAssignment.production_id, _t("أمر إنتاج")),
                modelName: relationName(rawAssignment.furniture_model_id),
                stage,
                stageLabel: STAGE_DEFINITION_BY_CODE[stage]?.label || stage || _t("مرحلة"),
                stageClass: `is-stage-${stage || "other"}`,
                operationName: String(rawAssignment.operation_name || _t("مهمة تشغيل")),
                outputSummary: String(rawAssignment.output_summary || ""),
                outputLines,
                pieceCount: assignmentPieceCount,
                plannedHours: assignmentHours,
                plannedStart: rawAssignment.planned_start,
                plannedEnd: rawAssignment.planned_end,
                state: String(rawAssignment.state || "proposed"),
                stateLabel: MPS_ASSIGNMENT_STATE_LABELS[rawAssignment.state] || rawAssignment.state,
                stateClass: `is-state-${rawAssignment.state || "proposed"}`,
            };
            assignments.push(assignment);
        }
        this.dashboard.assignments = assignments;
        this.dashboard.sourceAssignmentCount = assignments.length;
        this._rebuildDashboard();
    }

    _dateBoundary(value, endOfDay = false) {
        const parts = String(value || "").split("-").map(Number);
        if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) {
            return false;
        }
        return new Date(
            parts[0],
            parts[1] - 1,
            parts[2],
            endOfDay ? 23 : 0,
            endOfDay ? 59 : 0,
            endOfDay ? 59 : 0,
            endOfDay ? 999 : 0
        );
    }

    _assignmentMatchesFilters(assignment) {
        if (this.filters.stage && assignment.stage !== this.filters.stage) {
            return false;
        }
        if (this.filters.state && assignment.state !== this.filters.state) {
            return false;
        }
        const assignmentStart = parseServerDate(assignment.plannedStart);
        const assignmentEnd = parseServerDate(assignment.plannedEnd);
        const dateFrom = this._dateBoundary(this.filters.dateFrom);
        const dateTo = this._dateBoundary(this.filters.dateTo, true);
        if (dateFrom && assignmentEnd && assignmentEnd < dateFrom) {
            return false;
        }
        if (dateTo && assignmentStart && assignmentStart > dateTo) {
            return false;
        }
        return true;
    }

    _rebuildDashboard() {
        const visibleAssignments = this.dashboard.assignments.filter(
            (assignment) => this._assignmentMatchesFilters(assignment)
        );
        const workersById = new Map();
        let pieceCount = 0;
        let plannedHours = 0;
        for (const assignment of visibleAssignments) {
            let worker = workersById.get(assignment.employeeId);
            if (!worker) {
                worker = {
                    key: assignment.employeeId || `worker-${assignment.id}`,
                    name: assignment.employeeName,
                    assignments: [],
                    pieceCount: 0,
                    plannedHours: 0,
                    stages: new Map(),
                };
                workersById.set(assignment.employeeId, worker);
            }
            worker.assignments.push(assignment);
            worker.pieceCount += assignment.pieceCount;
            worker.plannedHours += assignment.plannedHours;
            worker.stages.set(assignment.stage, assignment.stageLabel);
            pieceCount += assignment.pieceCount;
            plannedHours += assignment.plannedHours;
        }

        const workers = [...workersById.values()]
            .map((worker) => ({
                ...worker,
                stages: [...worker.stages.entries()].map(([code, label]) => ({
                    code,
                    label,
                    className: `is-stage-${code || "other"}`,
                })),
            }))
            .sort((left, right) => left.name.localeCompare(right.name));
        this.dashboard.workers = workers;
        this.dashboard.assignmentCount = visibleAssignments.length;
        this.dashboard.pieceCount = pieceCount;
        this.dashboard.plannedHours = plannedHours;
    }

    onStageFilterChange(event) {
        this.filters.stage = String(event.target.value || "");
        this._rebuildDashboard();
    }

    onStateFilterChange(event) {
        this.filters.state = String(event.target.value || "");
        this._rebuildDashboard();
    }

    onDateFromChange(event) {
        this.filters.dateFrom = String(event.target.value || "");
        if (
            this.filters.dateFrom &&
            this.filters.dateTo &&
            this.filters.dateFrom > this.filters.dateTo
        ) {
            this.filters.dateTo = this.filters.dateFrom;
        }
        this._rebuildDashboard();
    }

    onDateToChange(event) {
        this.filters.dateTo = String(event.target.value || "");
        if (
            this.filters.dateFrom &&
            this.filters.dateTo &&
            this.filters.dateFrom > this.filters.dateTo
        ) {
            this.filters.dateFrom = this.filters.dateTo;
        }
        this._rebuildDashboard();
    }

    showCurrentFactoryWeek() {
        const range = currentFactoryWeekRange();
        this.filters.dateFrom = range.dateFrom;
        this.filters.dateTo = range.dateTo;
        this._rebuildDashboard();
    }

    clearFilters() {
        this.filters.stage = "";
        this.filters.state = "";
        this.filters.dateFrom = "";
        this.filters.dateTo = "";
        this._rebuildDashboard();
    }

    formatQuantity(value) {
        return this.quantityFormatter.format(Math.max(0, Math.round(asNumber(value))));
    }

    formatHours(value) {
        return this.hoursFormatter.format(Math.max(0, asNumber(value)));
    }

    formatDateTime(value) {
        if (!value) {
            return _t("غير محدد");
        }
        const date = parseServerDate(value);
        if (!date) {
            return String(value);
        }
        return new Intl.DateTimeFormat(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
        }).format(date);
    }

    async refresh() {
        await this.loadDashboard();
    }

    async openPlans() {
        if (this.permissions.isManager) {
            await this.actionService.doAction(
                "furniture_mrp.action_furniture_mrp_mps_plans"
            );
        }
    }

    async openTimings() {
        if (this.permissions.isManager) {
            await this.actionService.doAction(
                "furniture_mrp.action_furniture_mrp_mps_product_times"
            );
        }
    }
}

registry.category("actions").add(
    "furniture_mrp_mps_dashboard_page",
    FurnitureMrpMPSDashboardPage
);
