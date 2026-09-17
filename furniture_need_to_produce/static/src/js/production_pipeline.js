/** @odoo-module **/
import { Component, onMounted, onWillDestroy, onWillStart, useExternalListener, useState, useSubEnv, useRef } from "@odoo/owl";
import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { imageUrl } from "@web/core/utils/urls";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Domain } from "@web/core/domain";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";
import { usePager } from "@web/search/pager_hook";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { STAGE_SHORTAGE_OPTIONS, stageShortageAction } from "./stage_shortages";
import { compareFurniturePieces } from "@furniture_stage_replenishment/js/stage_replenishment_dashboard";

/** Keep IDs distinct even when two models have the same display name. */
export function pipelineModelOptions(groups) {
    const options = new Map();
    for (const group of groups) {
        const model = group.furniture_model_id;
        if (Array.isArray(model) && Number.isInteger(model[0]) && model[0] > 0) {
            options.set(model[0], { id: model[0], name: String(model[1] || model[0]) });
        }
    }
    return [...options.values()].sort((a, b) => a.name.localeCompare(b.name, "ar") || a.id - b.id);
}

/** Invisible custom filters still have a removable native search-bar facet. */
export function activePipelineModelFilters(searchModel) {
    return searchModel.query
        .map(({ searchItemId }) => searchModel.searchItems[searchItemId])
        .filter((item) => Number.isInteger(item?.ntpPipelineModelId));
}

/** Native record selection is page-scoped and resets when the domain reloads. */
export class ProductionPieceSelector extends Component {
    static template = "furniture_need_to_produce.PieceSelector";
    static props = { ...standardFieldProps };

    setup() {
        // Subscribe this field too, so its disabled state follows the dialog.
        this.reactiveBulk = useState(this.env.ntpPipelineBulk || {});
    }

    get bulkState() {
        return this.reactiveBulk || this.env.ntpPipelineBulk;
    }

    get pieceRecords() { return this.env.ntpDisplayGroup?.(this.props.record) || [this.props.record]; }
    get allSelected() { return this.pieceRecords.every(record => record.selected); }

    async onChange(event) {
        if (!this.bulkState?.canApprove || this.bulkState.busy || this.bulkState.confirming ||
            this.props.record.data.state !== "draft") {
            event.target.checked = this.allSelected;
            return;
        }
        await Promise.all(this.pieceRecords.map(record => record.toggleSelection(event.target.checked)));
    }
}

/** The visible card describes the full group, never its representative ID. */
export class ProductionPieceTitle extends Component {
    static template = "furniture_need_to_produce.PieceTitle";
    static props = { ...standardFieldProps };
    get count() { return (this.env.ntpDisplayGroup?.(this.props.record) || [this.props.record]).length; }
}
export class ProductionPieceReference extends ProductionPieceTitle {
    static template = "furniture_need_to_produce.PieceReference";
}
export class ProductionPieceDetails extends ProductionPieceTitle {
    static template = "furniture_need_to_produce.PieceDetails";
}
export class ProductionPieceTime extends Component {
    static template = "furniture_need_to_produce.PieceTime";
    static props = { ...standardFieldProps };
    get value() {
        return new Intl.NumberFormat("en-US", {maximumFractionDigits: 2, useGrouping: false})
            .format(this.props.record.data[this.props.name] || 0);
    }
}

/** Group only loaded, authorized native records; selection still uses real IDs. */
export function groupIdenticalPieces(records) {
    const groups = new Map();
    for (const record of records) {
        const data = record.data;
        const signature = data.state === "draft" && !data.is_custom && !data.custom_bom_modified
            && data.route_graph?.display_group_key;
        const key = signature ? `same:${signature}` : `piece:${record.resId}`;
        if (!groups.has(key)) groups.set(key, {key, records: []});
        groups.get(key).records.push(record);
    }
    return [...groups.values()];
}

export class GroupedPieceRenderer extends kanbanView.Renderer {
    static template = "furniture_need_to_produce.GroupedPieceRenderer";
    setup() {
        super.setup();
        this.pieceGroups = useState({expanded: {}});
        useSubEnv({ntpDisplayGroup: record => this.displayGroup(record),
            ntpExpandDisplayGroup: record => this.expandForEdit(record)});
    }
    displayGroup(record) {
        if (this.props.list.isGrouped) return [record];
        const group = groupIdenticalPieces(this.props.list.records).find(g => g.records.some(member => member.resId === record.resId));
        return group && !this.pieceGroups.expanded[group.key] ? group.records : [record];
    }
    expandForEdit(record) {
        const group = groupIdenticalPieces(this.props.list.records).find(g => g.records.some(member => member.resId === record.resId));
        if (!group || group.records.length < 2 || this.pieceGroups.expanded[group.key]) return false;
        this.pieceGroups.expanded[group.key] = true;
        return true;
    }
    openPieceRecord(record) {
        if (!this.expandForEdit(record)) return this.props.openRecord(record);
    }
    togglePieceGroup(key) { this.pieceGroups.expanded[key] = !this.pieceGroups.expanded[key]; }
    getGroupsOrRecords() {
        if (this.props.list.isGrouped) return super.getGroupsOrRecords();
        return groupIdenticalPieces(this.props.list.records).flatMap(group => {
            const expanded = Boolean(this.pieceGroups.expanded[group.key]);
            return (expanded ? group.records : group.records.slice(0, 1)).map((record, index) => ({
                // Remount native fields when switching group/individual scope;
                // never reuse a checkbox's browser state from the other scope.
                record, key: `${record.id}:${expanded ? 'individual' : group.key}`, pieceGroup: group,
                showGroupHeader: index === 0 && group.records.length > 1,
                expanded,
            }));
        });
    }
}

/** Read-only BoM customization indicator; never an approval or a toggle. */
export class ProductionPieceCustom extends Component {
    static template = "furniture_need_to_produce.PieceCustom";
    static props = { ...standardFieldProps };
}

export class ProductionPipelineController extends KanbanController {
    static template = "furniture_need_to_produce.PipelineView";

    setup() {
        super.setup();
        this.pipelineOrm = useService("orm");
        this.pipelineDialog = useService("dialog");
        this.pipelineNotification = useService("notification");
        this.pipelineBulk = useState({ busy: false, confirming: false, canApprove: false });
        useSubEnv({ ntpPipelineBulk: this.pipelineBulk });
        onWillStart(async () => {
            this.pipelineBulk.canApprove = await user.hasGroup("furniture_mrp.group_furniture_mrp_manager");
        });
        this.pipelineFilters = useState({
            options: [],
            modelId: "",
            modelName: "",
            loading: true,
            loadError: false,
        });
        this.pipelineDestroyed = false;
        this.pipelineOptionsRequest = 0;
        this.syncPipelineModelFilter();
        useBus(this.env.searchModel, "update", () => this.syncPipelineModelFilter());
        onMounted(() => this.loadPipelineModelOptions());
        onWillDestroy(() => {
            this.pipelineDestroyed = true;
            this.pipelineOptionsRequest++;
        });
    }

    get className() {
        return `${super.className || ""} o_ntp_pipeline_view`;
    }

    get pipelineSelectableRecords() {
        return this.model.root.records.filter((record) => record.data.state === "draft");
    }

    get pipelineSelectedRecords() {
        return this.pipelineSelectableRecords.filter((record) => record.selected);
    }

    get pipelinePageSelected() {
        const records = this.pipelineSelectableRecords;
        return records.length > 0 && records.every((record) => record.selected);
    }

    async togglePipelinePage() {
        if (this.pipelineBulk.busy || this.pipelineBulk.confirming) return;
        const selected = !this.pipelinePageSelected;
        await Promise.all(this.pipelineSelectableRecords.map((record) => record.toggleSelection(selected)));
    }

    async clearPipelineSelection() {
        if (this.pipelineBulk.busy || this.pipelineBulk.confirming) return;
        await Promise.all(this.model.root.selection.map((record) => record.toggleSelection(false)));
    }

    approvePipelineSelection() {
        const ids = [...new Set(this.pipelineSelectedRecords.map((record) => record.resId))];
        this.confirmPipelineIds(ids, "القطع المحددة");
    }

    confirmPipelineIds(selectedIds, scope) {
        const ids = [...new Set(selectedIds)].filter((id) => Number.isInteger(id) && id > 0);
        if (!ids.length || !this.pipelineBulk.canApprove || this.pipelineBulk.busy || this.pipelineBulk.confirming) return;
        // Freeze the explicit page/group selection before confirmation.
        // Never re-expand a changing domain or issue one transaction per card.
        const context = { ...this.model.root.context };
        this.pipelineBulk.confirming = true;
        this.pipelineDialog.add(ConfirmationDialog, {
            title: `اعتماد ${scope}`,
            body: `اعتماد ${ids.length} قطعة من ${scope} وإنشاء أوامر مراحلها المطلوبة؟`,
            confirmLabel: `Approve (${ids.length})`,
            cancelLabel: "إلغاء",
            cancel: () => {},
            confirm: async () => {
                this.pipelineBulk.busy = true;
                try {
                    await this.pipelineOrm.call(this.props.resModel, "action_approve", [ids], { context });
                    if (!this.pipelineDestroyed) {
                        await this.model.root.load();
                        await this.refreshWorkspace?.();
                        this.pipelineNotification.add(`تم اعتماد ${ids.length} قطعة.`, { type: "success" });
                    }
                } finally {
                    this.pipelineBulk.busy = false;
                }
            },
        }, { onClose: () => { this.pipelineBulk.confirming = false; } });
    }

    get pipelineModelOptions() {
        const { options, modelId, modelName } = this.pipelineFilters;
        // Retain a restored facet's label even if its last piece was removed.
        if (modelId && !options.some((option) => String(option.id) === modelId)) {
            return [{ id: Number(modelId), name: modelName }, ...options];
        }
        return options;
    }

    async loadPipelineModelOptions() {
        const request = ++this.pipelineOptionsRequest;
        this.pipelineFilters.loading = true;
        this.pipelineFilters.loadError = false;
        try {
            // Aggregate all accessible pieces, not only the 12 loaded cards.
            // Keep action/company scope, but not the current model/state facets,
            // so another model remains selectable after filtering.
            const groups = await this.pipelineOrm.readGroup(
                this.props.resModel,
                this.env.searchModel.globalDomain,
                ["furniture_model_id"],
                ["furniture_model_id"],
                { context: this.env.searchModel.globalContext, lazy: false }
            );
            if (!this.pipelineDestroyed && request === this.pipelineOptionsRequest) {
                this.pipelineFilters.options = pipelineModelOptions(groups);
            }
        } catch {
            if (!this.pipelineDestroyed && request === this.pipelineOptionsRequest) {
                this.pipelineFilters.loadError = true;
            }
        } finally {
            if (!this.pipelineDestroyed && request === this.pipelineOptionsRequest) {
                this.pipelineFilters.loading = false;
            }
        }
    }

    syncPipelineModelFilter() {
        const filters = activePipelineModelFilters(this.env.searchModel);
        const filter = filters[filters.length - 1];
        this.pipelineFilters.modelId = filter ? String(filter.ntpPipelineModelId) : "";
        this.pipelineFilters.modelName = filter?.ntpPipelineModelName || "";
    }

    onPipelineModelChange(event) {
        this.setPipelineModelFilter(event.target.value);
    }

    clearPipelineModelFilter() {
        this.setPipelineModelFilter("");
    }

    setPipelineModelFilter(value) {
        const option = this.pipelineModelOptions.find((option) => String(option.id) === value);
        if (value && !option) {
            return;
        }
        const searchModel = this.env.searchModel;
        const activeFilters = activePipelineModelFilters(searchModel);
        if (activeFilters.length === 1 && value === String(activeFilters[0].ntpPipelineModelId)) {
            return;
        }
        // Each selector facet owns its group. Never clear the user's other
        // filters (approval state, product, manual model searches, favorites).
        for (const groupId of new Set(activeFilters.map((filter) => filter.groupId))) {
            searchModel.deactivateGroup(groupId);
        }
        if (option) {
            searchModel.createNewFilters([{
                description: `الموديل: ${option.name}`,
                domain: JSON.stringify([["furniture_model_id", "=", option.id]]),
                invisible: "True",
                ntpPipelineModelId: option.id,
                ntpPipelineModelName: option.name,
            }]);
        }
        this.syncPipelineModelFilter();
    }
}

/** Read-only finite-capacity estimate, NOT an MPS/worker assignment.
 * One piece per operation at a time; different operations run concurrently.
 * Only saved new work is scheduled: stock/history add no work, incoming waits
 * stay explicitly unknown. No allocation or BoM data is modified here.
 */
export function estimatePipelineGroup(pieces) {
    const tasks = new Map();
    let unknown = false;
    for (const piece of pieces) {
        const preview = piece.preview_json;
        if (!preview || !Array.isArray(preview.stages)) return { hours: null, days: null, unknown: true };
        unknown ||= Boolean(piece.unknown_incoming_wait);
        const ends = new Map();
        for (const stage of preview.stages) {
            const operations = stage.operations?.length ? stage.operations :
                (stage.stage_codes || [stage.stage_code]).map(code => ({
                    stage_code: code, estimated_hours: stage.estimated_hours / (stage.stage_codes?.length || 1),
                }));
            if (!stage.key || ends.has(stage.key) || !operations.length) return { hours: null, days: null, unknown: true };
            const predecessors = [];
            for (const dependency of stage.dependencies || []) {
                if (!ends.has(dependency)) return { hours: null, days: null, unknown: true };
                predecessors.push(...ends.get(dependency));
            }
            let prior = null;
            const operationIds = [];
            for (const [index, operation] of operations.entries()) {
                const hours = Number(operation.estimated_hours);
                if (!operation.stage_code || !Number.isFinite(hours) || hours < 0) return { hours: null, days: null, unknown: true };
                const id = `${piece.id}:${stage.key}:${index}`;
                if (tasks.has(id)) return { hours: null, days: null, unknown: true };
                tasks.set(id, {id, hours, resource: `${piece.company_id?.[0] || 0}:${operation.stage_code}`,
                    dependencies: stage.parallel_operations ? predecessors : (prior ? [prior] : predecessors), rank: tasks.size});
                prior = id;
                operationIds.push(id);
            }
            ends.set(stage.key, stage.parallel_operations ? operationIds : [prior]);
        }
    }
    const completed = new Map(), resources = new Map();
    let finish = 0;
    while (tasks.size) {
        let chosen = null, start = Infinity;
        for (const task of tasks.values()) {
            if (!task.dependencies.every(id => completed.has(id))) continue;
            const available = Math.max(resources.get(task.resource) || 0,
                ...task.dependencies.map(id => completed.get(id)), 0);
            if (available < start || (available === start && task.rank < chosen.rank)) {
                chosen = task; start = available;
            }
        }
        if (!chosen) return { hours: null, days: null, unknown: true };
        const end = start + chosen.hours;
        completed.set(chosen.id, end);
        resources.set(chosen.resource, end);
        finish = Math.max(finish, end);
        tasks.delete(chosen.id);
    }
    return {hours: finish, days: finish / 10, unknown};
}

// Match approval to the same company/product/replenishment destination only.
// Completed or cancelled history is not an outstanding partial approval.
function pipelineApprovalKey(row) {
    return JSON.stringify([row.company_id?.[0] || 0, row.product_id?.[0] || 0,
        row.target_lane || "", row.final_rule_id?.[0] || 0, row.buffer_rule_id?.[0] || 0]);
}

export function groupPipelineProducts(rows, approvedGroups = []) {
    const groups = new Map();
    for (const row of rows) {
        const [id, name] = row.product_id || [];
        if (!Number.isInteger(id)) continue;
        if (!groups.has(id)) groups.set(id, {id, name, rows: []});
        groups.get(id).rows.push(row);
    }
    return [...groups.values()].map(group => {
        const policies = new Set(group.rows.filter(row => row.state === "draft").map(pipelineApprovalKey));
        const approvedCount = approvedGroups.filter(row => policies.has(pipelineApprovalKey(row)))
            .reduce((count, row) => count + Math.max(0, Number(row.__count) || 0), 0);
        return {...group, time: estimatePipelineGroup(group.rows), approvedCount,
            isPartiallyApproved: policies.size > 0 && approvedCount > 0};
    })
        .sort((a, b) => compareFurniturePieces(a.name, b.name) || a.id - b.id);
}

/** Decorative category sketches only; never imply a saved product photo. */
export function workspaceIllustration(name = "") {
    const value = String(name || "").toLocaleLowerCase();
    if (/شاز|شيزل|chaise/.test(value)) return "chaise";
    if (/فوت|كرسي|كرسى|armchair|chair/.test(value)) return "armchair";
    if (/كنب|sofa|couch/.test(value)) return "sofa";
    return "collection";
}

/** Stable model identity colors; unrelated to production/stock status colors. */
export function workspaceModelTheme(modelId) {
    if (typeof modelId !== "number" && typeof modelId !== "string") return "";
    const id = Number(modelId);
    if (!Number.isSafeInteger(id) || id <= 0) return "";
    // Golden-angle spacing separates neighboring IDs without a finite palette
    // or dependence on sort order, translated names, or visible search results.
    const hue = (87 + id * 137.50776405) % 360;
    return `--ntp-model-hue:${hue.toFixed(2)};--ntp-model-accent-hue:${((hue + 180) % 360).toFixed(2)}`;
}

/** Leave native Enter behavior intact in editors, links and action buttons. */
export function isWorkspaceTopKey(event) {
    if (event.key !== "Enter" || event.defaultPrevented || event.isComposing || event.keyCode === 229 ||
        event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
    const target = event.target;
    const pieceCheckbox = target?.matches?.('.o_ntp_piece_selector input[type="checkbox"]');
    return !target?.closest?.('button, a, select, textarea, [role="button"], [role="textbox"], [contenteditable]:not([contenteditable="false"])') &&
        (pieceCheckbox || !target?.closest?.('input'));
}

/** Model -> product -> pieces. The existing piece/graph renderer stays intact. */
export class WorkspacePhoto extends Component {
    static template = "furniture_need_to_produce.WorkspacePhoto";
    static props = {resModel: String, resId: Number, title: String, kind: String,
        onNavigate: {type: Function, optional: true}, readonly: {type: Boolean, optional: true}};

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.viewer = useFileViewer();
        this.input = useRef("photoInput");
        this.photo = useState({exists: false, version: "", busy: false, canWrite: false});
        this.alive = true;
        onWillDestroy(() => { this.alive = false; });
        onWillStart(async () => {
            try {
                const [rows, canWrite] = await Promise.all([
                    this.orm.read(this.props.resModel, [this.props.resId], ["image_1920", "write_date"], {context: {bin_size: true}}),
                    this.props.readonly ? false : this.orm.call(this.props.resModel, "check_access_rights", ["write", false]),
                ]);
                if (this.alive) Object.assign(this.photo, {exists: Boolean(rows[0]?.image_1920),
                    version: rows[0]?.write_date || "", canWrite});
            } catch { /* Keep the illustration if image access is unavailable. */ }
        });
    }

    get url() { return imageUrl(this.props.resModel, this.props.resId, "image_1920", {unique: this.photo.version}); }

    preview() {
        if (!this.photo.exists) return this.props.onNavigate?.();
        this.viewer.open({name: this.props.title, displayName: this.props.title, mimetype: "image/png",
            isImage: true, isViewable: true, defaultSource: this.url, downloadUrl: this.url});
    }

    async upload(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file || this.photo.busy) return;
        if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 8 * 1024 * 1024) {
            this.notification.add("اختار صورة JPG أو PNG أو WebP بحجم أقصى 8 ميجابايت.", {type: "warning"});
            return;
        }
        this.photo.busy = true;
        try {
            const data = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(",")[1]);
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
            if (!this.alive) return;
            // Native write enforces model ACLs, record rules and Image validation.
            await this.orm.write(this.props.resModel, [this.props.resId], {image_1920: data});
            if (this.alive) Object.assign(this.photo, {exists: true, version: String(Date.now())});
        } catch {
            if (this.alive) this.notification.add("تعذّر حفظ الصورة. تأكد من صلاحية التعديل وصحة الملف.", {type: "danger"});
        } finally { if (this.alive) this.photo.busy = false; }
    }
}

export class ProductionWorkspaceController extends ProductionPipelineController {
    static components = {...ProductionPipelineController.components, WorkspacePhoto};
    get stageShortageOptions() { return STAGE_SHORTAGE_OPTIONS; }

    get stageShortageLane() { return this.env.searchModel.globalContext.ntp_stage_shortages || ""; }

    get stageShortageLabel() {
        return STAGE_SHORTAGE_OPTIONS.find(stage => stage.lane === this.stageShortageLane)?.label || "";
    }

    async openStageShortages(stage) {
        if (this.pipelineBulk.busy || this.pipelineBulk.confirming || stage.lane === this.stageShortageLane) return;
        const action = stageShortageAction(stage.code);
        if (action) return this.actionService.doAction(action, {clearBreadcrumbs: true});
    }

    setup() {
        super.setup();
        this.workspace = useState({productId: 0, rows: [], groups: [], selectedProducts: [],
            time: {hours: 0, days: 0, unknown: false}, loading: false, error: false});
        this.workspaceRequest = 0;
        this.syncPipelineModelFilter();
        this.workspaceReturnHandled = false;
        this.workspaceStickyFrame = 0;
        useExternalListener(window, "keydown", event => this.onWorkspaceTopKeyDown(event), {capture: true});
        useExternalListener(window, "keyup", event => this.onWorkspaceTopKeyUp(event), {capture: true});
        useExternalListener(window, "blur", () => { this.workspaceReturnHandled = false; });
        useExternalListener(window, "resize", () => this.scheduleWorkspaceToolbarTop());
        useExternalListener(window, "scroll", event => {
            if (event.target === this.rootRef.el) this.scheduleWorkspaceToolbarTop();
        }, {capture: true, passive: true});
        onMounted(() => this.scheduleWorkspaceToolbarTop());
        // Summary steps have no piece pagination. Keep the native pager only
        // inside a product, including selection reset and scroll behavior.
        usePager(() => {
            if (!this.workspace.productId || this.workspace.loading) return;
            const root = this.model.root;
            if (root.isGrouped || this.model.useSampleModel) return;
            return {offset: root.offset, limit: root.limit, total: root.count,
                onUpdate: async ({offset, limit}, navigated) => {
                    await root.load({offset, limit});
                    await this.onUpdatedPager();
                    if (navigated) this.onPageChangeScroll();
                }, updateTotal: root.hasLimitedCount ? () => root.fetchCount() : undefined};
        });
        onWillDestroy(() => {
            this.workspaceRequest++;
            cancelAnimationFrame(this.workspaceStickyFrame);
        });
    }

    scheduleWorkspaceToolbarTop() {
        cancelAnimationFrame(this.workspaceStickyFrame);
        this.workspaceStickyFrame = requestAnimationFrame(() => {
            const root = this.rootRef.el;
            if (!root) return;
            const panel = root.querySelector(".o_control_panel");
            // On mobile Odoo can slide the control panel in/out of the same
            // scroller. Keep the selection bar below only its visible portion.
            const panelRect = panel?.getBoundingClientRect();
            const top = this.env.isSmall && panelRect
                ? Math.max(0, Math.min(panelRect.height, panelRect.bottom - root.getBoundingClientRect().top)) : 0;
            root.style.setProperty("--ntp-selection-top", `${top}px`);
        });
    }

    onWorkspaceTopKeyDown(event) {
        const root = this.rootRef.el;
        if (!isWorkspaceTopKey(event) || !root?.isConnected || !root.getClientRects().length ||
            this.pipelineDestroyed || this.env.inDialog || this.pipelineBulk.busy || this.pipelineBulk.confirming ||
            document.querySelector('.o_dialog, .modal.show, [role="dialog"]')) return;
        if (!root.contains(event.target) && event.target !== document.body && event.target !== document.documentElement) return;
        event.preventDefault();
        event.stopPropagation();
        this.workspaceReturnHandled = true;
        this.scrollWorkspaceToTop();
    }

    scrollWorkspaceToTop() {
        const root = this.rootRef.el;
        // Animate this shortcut only; keep native pager/navigation scrolling unchanged.
        const scroller = this.env.isSmall ? root : root?.querySelector(".o_content");
        scroller?.scrollTo({top: 0,
            behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"});
    }

    onWorkspaceTopKeyUp(event) {
        if (event.key !== "Enter" || !this.workspaceReturnHandled) return;
        this.workspaceReturnHandled = false;
        event.preventDefault();
        event.stopPropagation();
    }

    get className() {
        return `${super.className} o_ntp_workspace ${this.workspace?.productId ? "is-piece-level" : "is-group-level"}`;
    }

    get workspaceProduct() {
        return this.workspace.groups.find(group => group.id === this.workspace.productId);
    }

    get workspaceLeafReady() {
        return this.workspace.productId && !this.workspace.loading && !this.workspace.error &&
            (this.model.root.records || []).every(record => record.data.product_id?.[0] === this.workspace.productId &&
                record.data.furniture_model_id?.[0] === Number(this.pipelineFilters.modelId) && record.data.state === "draft");
    }

    get workspaceSelectedPieces() {
        // Do not reuse a previous product's selection while navigation reloads.
        return this.workspaceLeafReady ? this.pipelineSelectedRecords : [];
    }

    get workspaceApprovalLabel() {
        if (this.workspaceSelectedPieces.length) return "Approve المحدد";
        return this.workspace.productId ? "Approve الصنف كله" : "Approve الموديل كله";
    }

    approveWorkspaceScope() {
        if (!this.pipelineBulk.canApprove || this.pipelineBulk.busy || this.pipelineBulk.confirming ||
            this.pipelineFilters.loading || this.pipelineFilters.loadError || this.workspace.loading || this.workspace.error ||
            (this.workspace.productId && !this.workspaceLeafReady)) return;
        if (this.workspaceSelectedPieces.length) {
            return this.approvePipelineSelection();
        }
        return this.approveWorkspace(this.workspace.productId ? "product" : "model", this.workspace.productId);
    }

    formatTime(value) {
        return value === null || value === undefined ? "—" : Number(value).toLocaleString("en-US", {maximumFractionDigits: 2});
    }

    illustrationFor(name) { return workspaceIllustration(name); }

    modelThemeStyle(modelId) { return workspaceModelTheme(modelId); }

    hasPendingPieces(rows) { return (rows || []).some(row => row.state === "draft"); }

    syncPipelineModelFilter() {
        super.syncPipelineModelFilter();
        if (!this.workspace) return;
        const filters = activePipelineModelFilters(this.env.searchModel);
        this.workspace.productId = filters.at(-1)?.ntpPipelineProductId || 0;
    }

    async loadPipelineModelOptions() {
        const request = ++this.pipelineOptionsRequest;
        this.pipelineFilters.loading = true;
        this.pipelineFilters.loadError = false;
        try {
            const groups = await this.pipelineOrm.readGroup(this.props.resModel,
                Domain.and([this.env.searchModel.globalDomain, [["state", "=", "draft"]]]).toList(),
                ["furniture_model_id"], ["furniture_model_id"], {context: this.env.searchModel.globalContext, lazy: false});
            if (this.pipelineDestroyed || request !== this.pipelineOptionsRequest) return;
            this.pipelineFilters.options = pipelineModelOptions(groups);
            if (this.pipelineFilters.modelId) await this.loadWorkspace();
        } catch {
            if (!this.pipelineDestroyed && request === this.pipelineOptionsRequest) this.pipelineFilters.loadError = true;
        } finally {
            if (!this.pipelineDestroyed && request === this.pipelineOptionsRequest) this.pipelineFilters.loading = false;
        }
    }

    async loadWorkspace() {
        const request = ++this.workspaceRequest;
        const modelId = Number(this.pipelineFilters.modelId);
        this.workspace.selectedProducts = [];
        this.workspace.rows = []; this.workspace.groups = [];
        this.workspace.error = false;
        this.workspace.loading = Boolean(modelId);
        if (!modelId) return;
        try {
            const rows = [];
            let after = 0;
            while (true) {
                const page = await this.pipelineOrm.searchRead(this.props.resModel,
                    Domain.and([this.env.searchModel.globalDomain,
                        [["state", "=", "draft"], ["furniture_model_id", "=", modelId], ["id", ">", after]]]).toList(),
                    ["product_id", "company_id", "state", "preview_json", "unknown_incoming_wait",
                        "target_lane", "final_rule_id", "buffer_rule_id"],
                    {context: this.env.searchModel.globalContext, order: "id", limit: 500});
                if (this.pipelineDestroyed || request !== this.workspaceRequest) return;
                rows.push(...page);
                if (page.length < 500) break;
                after = page.at(-1).id;
            }
            const approvalFields = ["company_id", "product_id", "target_lane", "final_rule_id", "buffer_rule_id"];
            const approvedGroups = rows.length ? await this.pipelineOrm.readGroup(this.props.resModel,
                Domain.and([this.env.searchModel.globalDomain,
                    [["state", "=", "approved"], ["furniture_model_id", "=", modelId],
                        ["product_id", "in", [...new Set(rows.map(row => row.product_id[0]))]]]]).toList(),
                approvalFields, approvalFields, {context: this.env.searchModel.globalContext, lazy: false}) : [];
            if (this.pipelineDestroyed || request !== this.workspaceRequest) return;
            const siblings = rows.length && this.env.searchModel.globalContext.ntp_stage_shortages
                ? await this.pipelineOrm.call(this.props.resModel, "stage_sibling_balances",
                    [rows.map(row => row.id)], {context: this.env.searchModel.globalContext}) : {};
            if (this.pipelineDestroyed || request !== this.workspaceRequest) return;
            this.workspace.rows = rows;
            this.workspace.groups = groupPipelineProducts(rows, approvedGroups).map(group => ({
                ...group,
                siblings: [...new Map(group.rows.flatMap(row => siblings[row.buffer_rule_id?.[0]] || [])
                    .map(sibling => [sibling.rule_id, sibling])).values()],
            }));
            this.workspace.time = estimatePipelineGroup(rows);
        } catch {
            if (!this.pipelineDestroyed && request === this.workspaceRequest) this.workspace.error = true;
        } finally {
            if (!this.pipelineDestroyed && request === this.workspaceRequest) this.workspace.loading = false;
        }
    }

    setWorkspaceLocation(modelId, productId = 0) {
        if (this.pipelineBulk.busy || this.pipelineBulk.confirming) return;
        const option = this.pipelineModelOptions.find(item => item.id === Number(modelId));
        if (modelId && !option) return;
        const modelChanged = String(modelId || "") !== this.pipelineFilters.modelId;
        const search = this.env.searchModel;
        // The guided workspace owns its navigation; old invisible list/search
        // facets must not silently hide pieces inside the chosen product.
        // Global action/company scope remains untouched.
        const groups = search.query.map(({searchItemId}) => search.searchItems[searchItemId]?.groupId);
        for (const id of new Set(groups.filter(id => id !== undefined))) search.deactivateGroup(id);
        if (option) {
            const domain = [["state", "=", "draft"], ["furniture_model_id", "=", option.id]];
            if (productId) domain.push(["product_id", "=", productId]);
            search.createNewFilters([{description: option.name, domain: JSON.stringify(domain), invisible: "True",
                ntpPipelineModelId: option.id, ntpPipelineModelName: option.name, ntpPipelineProductId: productId}]);
        }
        this.syncPipelineModelFilter();
        if (modelChanged) this.loadWorkspace();
    }

    onPipelineModelChange(event) { this.setWorkspaceLocation(Number(event.target.value)); }

    completeStageSibling(sibling) {
        if (!this.pipelineBulk.canApprove || this.pipelineBulk.busy || this.pipelineBulk.confirming || !sibling.missing) return;
        const context = {...this.env.searchModel.globalContext};
        this.pipelineBulk.confirming = true;
        this.pipelineDialog.add(ConfirmationDialog, {
            title: `استكمال ${sibling.name} في ${sibling.stage}`,
            body: `الرصيد ${this.formatTime(sibling.total)} قطعة (المخزون + أوامر الإنتاج). إنشاء أوامر لاستكمال ${this.formatTime(sibling.missing)} قطعة حتى الماكس ${this.formatTime(sibling.max)}؟ يُراجع الرصيد مرة أخرى قبل الإنشاء.`,
            confirmLabel: "استكمال وإنشاء الأوامر",
            cancelLabel: "إلغاء",
            cancel: () => {},
            confirm: async () => {
                this.pipelineBulk.busy = true;
                try {
                    const result = await this.pipelineOrm.call(this.props.resModel, "action_complete_stage_sibling",
                        [[sibling.source_piece_id], sibling.rule_id], {context});
                    if (!this.pipelineDestroyed) {
                        await this.model.root.load();
                        await this.refreshWorkspace();
                        this.pipelineNotification.add(result.quantity
                            ? `تم إنشاء أوامر استكمال ${this.formatTime(result.quantity)} قطعة من ${sibling.name}.`
                            : "الرصيد مكتمل بالفعل؛ لم تُنشأ أوامر إضافية.", {type: "success"});
                    }
                } finally {
                    this.pipelineBulk.busy = false;
                }
            },
        }, {onClose: () => { this.pipelineBulk.confirming = false; }});
    }
    clearPipelineModelFilter() { this.setWorkspaceLocation(0); }
    showWorkspaceProducts() { this.setWorkspaceLocation(Number(this.pipelineFilters.modelId)); }
    openWorkspaceProduct(group) { this.setWorkspaceLocation(Number(this.pipelineFilters.modelId), group.id); }

    toggleWorkspaceProduct(id, checked) {
        if (this.pipelineBulk.busy || this.pipelineBulk.confirming || !this.pipelineBulk.canApprove) return;
        const selected = new Set(this.workspace.selectedProducts);
        if (checked) selected.add(id); else selected.delete(id);
        this.workspace.selectedProducts = [...selected];
    }

    get workspaceSelectionCount() {
        return this.workspace.groups.filter(group => this.workspace.selectedProducts.includes(group.id))
            .reduce((count, group) => count + group.rows.length, 0);
    }

    approveWorkspace(kind, productId = 0) {
        if (this.workspace.loading || this.workspace.error) return;
        const rows = this.workspace.rows.filter(row => kind === "model" ||
            (kind === "product" ? row.product_id[0] === productId : this.workspace.selectedProducts.includes(row.product_id[0])));
        const product = this.workspace.groups.find(group => group.id === productId);
        const label = kind === "model" ? this.pipelineFilters.modelName : kind === "product" ? product?.name : "الأصناف المحددة";
        this.confirmPipelineIds(rows.map(row => row.id), label);
    }

    async refreshWorkspace() { await this.loadPipelineModelOptions(); }

    async afterExecuteActionButton(params) {
        await super.afterExecuteActionButton(params);
        if (!this.pipelineDestroyed) await this.refreshWorkspace();
    }
}

registry.category("fields").add("need_produce_selector", {
    component: ProductionPieceSelector,
    supportedTypes: ["selection"],
});
registry.category("fields").add("need_produce_title", {
    component: ProductionPieceTitle, supportedTypes: ["many2one"],
});
registry.category("fields").add("need_produce_reference", {
    component: ProductionPieceReference, supportedTypes: ["char"],
});
registry.category("fields").add("need_produce_details", {
    component: ProductionPieceDetails, supportedTypes: ["char"],
});
registry.category("fields").add("need_produce_compact_time", {
    component: ProductionPieceTime, supportedTypes: ["float"],
});

registry.category("fields").add("need_produce_custom", {
    component: ProductionPieceCustom,
    supportedTypes: ["boolean"],
});

registry.category("views").add("need_produce_pipeline", {
    ...kanbanView,
    Controller: ProductionWorkspaceController,
    Renderer: GroupedPieceRenderer,
});
