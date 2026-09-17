/** @odoo-module **/

import { Component, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/** Display only: omit green stock/withdrawn nodes, retaining the server graph.
 * Unconsumed history is yellow, so it must stay visible along with incoming
 * orders and required operations. Remove incident edges before layout rather
 * than hiding cards with CSS (which leaves holes and dangling arrows).
 */
export function remainingNeedProduceGraph(graph) {
    const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
    let edges = Array.isArray(graph?.edges) ? [...graph.edges] : [];
    // A combined finish order receives one physical input and releases through
    // preparation. Bases are a completion gate, not a second stock delivery.
    // Match the exact shared stage record, never just translated lane labels.
    for (const base of nodes.filter(n => n.stage_code === "bases" && n.lane === "finish" && n.stage_id)) {
        const preparation = nodes.find(n => n.stage_code === "finishing" && n.lane === "finish"
            && n.stage_id === base.stage_id);
        if (!preparation) continue;
        edges = edges.filter(e => String(e.from) !== String(base.id) && String(e.to) !== String(base.id));
        edges.push({ from: base.id, to: preparation.id, completion: true });
    }
    const isGreen = node => node && (node.kind === "stock" || node.withdrawn);
    const hidden = new Set(nodes.filter(isGreen).map(node => String(node.id)));
    return {
        ...graph,
        nodes: nodes.filter(node => !isGreen(node)),
        edges: edges.filter(edge => !hidden.has(String(edge?.from ?? ""))
            && !hidden.has(String(edge?.to ?? ""))),
    };
}

/**
 * Lay out the server's dependency graph, never a parsed route description.
 *
 * Source -> destination is right -> left. Longest-path ranks preserve every
 * dependency; a reverse pass places short parallel branches near their join.
 * This pure function does not mutate its input and also tolerates stale edges,
 * duplicate IDs and cycles. Invalid edges are omitted and reported, not drawn
 * as if they were a valid production route.
 */
export function layoutNeedProduceGraph(graph, availableWidth = 0) {
    // The shortages view uses the same compact production-line language as the
    // approved tracker: connected circles with readable labels underneath.
    // Tailoring and painting remain separate rows and join only at their real
    // dependency, so the visual never flattens parallel work into one route.
    const nodeWidth = 140;
    const nodeHeight = 64;
    let columnGap = 20;
    const rowGap = 10;
    const padding = 8;
    const nodes = [];
    const byId = new Map();
    let invalid = false;
    for (const source of Array.isArray(graph?.nodes) ? graph.nodes : []) {
        if (!source || source.id === null || source.id === undefined || source.id === false) {
            invalid = true;
            continue;
        }
        const id = String(source.id);
        if (!id || byId.has(id)) {
            invalid = true;
            continue;
        }
        const node = { ...source, id, rank: 0, order: nodes.length, width: nodeWidth,
            height: nodeHeight,
            displayLabel: source.kind === "stage" ? source.label : String(source.label || "").split(" — ")[0] };
        nodes.push(node);
        byId.set(id, node);
    }
    const incoming = new Map(nodes.map((node) => [node.id, []]));
    const outgoing = new Map(nodes.map((node) => [node.id, []]));
    const validEdges = [];
    const seenEdges = new Set();
    for (const source of Array.isArray(graph?.edges) ? graph.edges : []) {
        const from = String(source?.from ?? "");
        const to = String(source?.to ?? "");
        const key = JSON.stringify([from, to]);
        if (!byId.has(from) || !byId.has(to) || from === to) {
            invalid = true;
            continue;
        }
        if (seenEdges.has(key)) {
            continue;
        }
        seenEdges.add(key);
        if (!source.completion) {
            incoming.get(to).push(from);
            outgoing.get(from).push(to);
        }
        validEdges.push({ from, to, key, completion: Boolean(source.completion) });
    }
    const degrees = new Map(nodes.map((node) => [node.id, incoming.get(node.id).length]));
    const queue = nodes.filter((node) => !degrees.get(node.id));
    const ordered = [];
    for (let index = 0; index < queue.length; index++) {
        const node = queue[index];
        ordered.push(node);
        for (const id of outgoing.get(node.id)) {
            const next = byId.get(id);
            next.rank = Math.max(next.rank, node.rank + 1);
            degrees.set(id, degrees.get(id) - 1);
            if (!degrees.get(id)) {
                queue.push(next);
            }
        }
    }
    const cyclicIds = new Set(nodes.filter((node) => degrees.get(node.id) > 0).map((node) => node.id));
    invalid ||= cyclicIds.size > 0;
    const maxRank = Math.max(0, ...nodes.map((node) => node.rank));
    if (maxRank && availableWidth > 0) {
        columnGap = Math.max(columnGap,
            (Math.min(1200, availableWidth - 4) - padding * 2 - (maxRank + 1) * nodeWidth) / maxRank);
    }
    for (const node of [...ordered].reverse()) {
        const children = outgoing.get(node.id).filter((id) => !cyclicIds.has(id));
        node.rank = children.length ? Math.min(...children.map((id) => byId.get(id).rank)) - 1 : maxRank;
    }
    // Preserve the graph's dependencies; lane names only stabilize visual rows.
    // Independent side inputs sit directly below their join, not all stacked
    // in the preceding column. Completion gates do not serialize operations.
    for (const node of nodes) {
        const gate = validEdges.find(e => e.from === node.id && e.completion);
        const children = outgoing.get(node.id);
        const sideInput = ["tailoring", "painting"].includes(node.stage_code || node.lane)
            && !incoming.get(node.id).length && children.length === 1;
        const target = gate ? byId.get(gate.to) : sideInput ? byId.get(children[0]) : null;
        if (target && !cyclicIds.has(node.id) && !cyclicIds.has(target.id)) {
            node.rank = target.rank;
            node.sideInput = true;
        }
    }
    const branchOrder = (node) => node.sideInput ? 3 : node.lane === "tailoring" ? 1 : node.lane === "painting" ? 2 : 0;
    const columns = new Map();
    for (const node of nodes) {
        if (cyclicIds.has(node.id)) {
            node.rank = 0;
        }
        if (!columns.has(node.rank)) {
            columns.set(node.rank, []);
        }
        columns.get(node.rank).push(node);
    }
    const rows = Math.max(1, ...[...columns.values()].map((column) => column.length));
    // All visible operations and waiting sources share the same compact face.
    const rowHeight = nodeHeight;
    const width = (maxRank + 1) * nodeWidth + maxRank * columnGap + padding * 2;
    const baseHeight = rows * rowHeight + (rows - 1) * rowGap + padding * 2;
    for (const [rank, column] of columns) {
        column.sort((a, b) => branchOrder(a) - branchOrder(b) || a.order - b.order);
        column.forEach((node, row) => {
            node.row = row;
            node.x = padding + (maxRank - rank) * (nodeWidth + columnGap);
            node.y = padding + row * (rowHeight + rowGap);
            node.hasInputs = incoming.get(node.id).length > 0;
            node.hasOutputs = outgoing.get(node.id).length > 0;
            node.parallel = row > 0;
            node.style = `left:${node.x}px;top:${node.y}px;width:${nodeWidth}px;height:${node.height}px`;
        });
    }
    let longEdges = 0;
    const edges = validEdges.filter((edge) => !cyclicIds.has(edge.from) && !cyclicIds.has(edge.to)).map((edge) => {
        const from = byId.get(edge.from);
        const to = byId.get(edge.to);
        const fromCenterX = from.x + nodeWidth / 2;
        const toCenterX = to.x + nodeWidth / 2;
        const markerCenterY = (node) => node.y + 16;
        if (from.rank === to.rank && from.row > to.row) {
            from.hasOutputs = false;
            from.hasTopOutput = true;
            to.hasBottomInput = true;
            // Bring side inputs around the labels, then into the join circle.
            const sideX = Math.min(width - 4, fromCenterX + nodeWidth / 2 + columnGap / 2);
            return { ...edge, path: `M ${fromCenterX + 17} ${markerCenterY(from)} H ${sideX} V ${markerCenterY(to)} H ${toCenterX + 18}`,
                kind: edge.completion ? "completion" : from.kind === "incoming" || from.kind === "history" ? "incoming" : "stage" };
        }
        const sx = fromCenterX - 17;
        const sy = markerCenterY(from);
        const tx = toCenterX + 17;
        const ty = markerCenterY(to);
        const midpoint = (sx + tx) / 2;
        let path = `M ${sx} ${sy} C ${midpoint} ${sy}, ${midpoint} ${ty}, ${tx} ${ty}`;
        if (to.rank - from.rank > 1) {
            // A shared dependency may skip columns. Route beneath nodes rather
            // than through another operation's card.
            const corridor = baseHeight + 10 + longEdges++ * 9;
            const startTurn = sx - columnGap / 2;
            const endTurn = tx + columnGap / 2;
            path = `M ${sx} ${sy} H ${startTurn} V ${corridor} H ${endTurn} V ${ty} H ${tx}`;
        }
        return { ...edge, path, kind: from.withdrawn ? "stock" : from.kind === "history" ? "incoming"
            : from.kind === "stock" || from.kind === "incoming" ? from.kind : "stage" };
    });
    return {
        nodes,
        edges,
        width,
        height: baseHeight + (longEdges ? 24 + longEdges * 9 : 0),
        invalid,
        hasParallel: rows > 1,
        hasCompletion: edges.some(edge => edge.completion),
    };
}

let graphSequence = 0;

export class NeedProduceGraphField extends Component {
    static template = "furniture_need_to_produce.ProductionRouteGraph";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.ui = useState({ opening: false, availableWidth: 0 });
        this.viewport = useRef("graphViewport");
        useEffect(() => {
            const element = this.viewport.el;
            if (!element) return;
            const updateWidth = () => { this.ui.availableWidth = element.clientWidth; };
            updateWidth();
            const observer = new ResizeObserver(updateWidth);
            observer.observe(element);
            return () => observer.disconnect();
        }, () => [this.viewport.el]);
        this.graphId = `ntp-route-${++graphSequence}`;
        this.alive = true;
        onWillUnmount(() => { this.alive = false; });
    }

    get graph() {
        return this.props.record.data[this.props.name] || {};
    }

    get layout() {
        return layoutNeedProduceGraph(remainingNeedProduceGraph(this.graph), this.ui.availableWidth);
    }

    get standaloneStage() {
        if (!this.props.record.data.buffer_rule_id) return null;
        const nodes = this.graph.nodes || [];
        // Independent operations keep BoM access without an empty diagram.
        return nodes.length === 1 && !(this.graph.edges || []).length &&
            nodes[0].kind === "stage" && ["priming", "tailoring", "painting"].includes(nodes[0].stage_code)
            ? nodes[0] : null;
    }

    format(value) {
        if (value === null || value === undefined || value === false || value === "") {
            return "—";
        }
        const number = Number(value);
        return Number.isFinite(number) ? String(Math.round(number * 100) / 100) : "—";
    }

    icon(node) {
        if (node.withdrawn) return "fa-check";
        if (node.kind === "history") return "fa-hourglass-half";
        if (node.kind === "stock") {
            return "fa-check-circle";
        }
        if (node.kind === "incoming") {
            return "fa-hourglass-half";
        }
        return {
            priming: "fa-paint-brush", carpentry: "fa-cubes", bases: "fa-th-large",
            finishing: "fa-wrench", tailoring: "fa-scissors", painting: "fa-tint",
            upholstery: "fa-diamond", packaging: "fa-archive",
        }[node.stage_code] || "fa-cog";
    }

    nodeDescription(node) {
        if (node.withdrawn) return `${node.label} — تم السحب للمرحلة التالية`;
        const label = node.displayLabel || node.label;
        if (node.kind === "incoming") {
            return `${label} — ينتظر أمرًا قائمًا — مدة الانتظار غير محددة — ${node.production_name || "أمر إنتاج جارٍ"}`;
        }
        if (node.kind === "history") {
            return `${label} — محجوز — لم يُسحب بعد — كمية الدفعة ${this.format(node.quantity)} — ${node.production_name || "مرحلة سابقة في المسار"}`;
        }
        return node.label;
    }

    canEdit(node) {
        return !node.withdrawn && node.kind === "stage" && Boolean(node.stage_id) && Boolean(node.editable)
            && this.props.record.data.state === "draft" && !node.production_id;
    }

    async openStage(node, event) {
        event?.stopPropagation();
        event?.preventDefault();
        // A shared diagram is not permission to edit an arbitrary member's BoM.
        if (this.env.ntpExpandDisplayGroup?.(this.props.record)) return;
        if (node.kind !== "stage" || !node.stage_id || this.ui.opening) {
            return;
        }
        this.ui.opening = node.id;
        const record = this.props.record;
        try {
            const result = await this.orm.call("furniture.need.to.produce.stage", "action_open", [[node.stage_id]]);
            if (!this.alive || !result) {
                return;
            }
            await this.action.doAction({
                ...result,
                target: "new",
                views: result.views || [[false, "form"]],
                context: { ...result.context, form_view_initial_mode: this.canEdit(node) ? "edit" : "readonly" },
            }, {
                onClose: async () => {
                    if (!this.alive) {
                        return;
                    }
                    try {
                        await record.load();
                    } catch {
                        if (this.alive) {
                            this.notification.add("تعذر تحديث مسار القطعة. أعد تحميل العرض لإظهار آخر التعديلات.", { type: "warning" });
                        }
                    }
                },
            });
        } catch {
            if (this.alive) {
                this.notification.add("تعذر فتح خامات المرحلة. حدّث العرض وحاول مرة أخرى.", { type: "danger" });
            }
        } finally {
            if (this.alive) {
                this.ui.opening = false;
            }
        }
    }
}

registry.category("fields").add("need_produce_graph", {
    component: NeedProduceGraphField,
    supportedTypes: ["json"],
    fieldDependencies: [{ name: "state", type: "selection" }],
});
