/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class DynamicAccountingReport extends Component {
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.state = useState({
            loading: true,
            reportType: this.props.action.context.report_type || 'gl',
            data: [],
            expanded: {},
            selected: {},
            dateFrom: '',
            dateTo: ''
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        this.state.loading = true;
        this.state.selected = {};
        try {
            const params = [this.state.dateFrom || null, this.state.dateTo || null];
            if (this.state.reportType === 'gl') {
                this.state.data = await this.orm.call("report.custom_accounting_pro.engine", "get_gl_data", params);
            } else if (this.state.reportType === 'pl') {
                this.state.data = await this.orm.call("report.custom_accounting_pro.engine", "get_pl_data", params);
            } else if (this.state.reportType === 'partner_ledger') {
                this.state.data = await this.orm.call("report.custom_accounting_pro.engine", "get_partner_ledger_data", params);
            } else if (this.state.reportType === 'trial_balance') {
                this.state.data = await this.orm.call("report.custom_accounting_pro.engine", "get_trial_balance_data", params);
            }
        } catch (e) {
            console.error("Error loading report data", e);
        } finally {
            this.state.loading = false;
        }
    }

    getSelectedKeys(specificData = null) {
        if (specificData) {
            return specificData.map(item => item.account_id || item.partner_id);
        }
        const hasSelection = Object.values(this.state.selected).some(v => v);
        if (hasSelection) {
            return Object.keys(this.state.selected)
                .filter(k => this.state.selected[k])
                .map(k => parseInt(k));
        }
        return null;
    }

    async exportExcel(specificData = null) {
        this.state.loading = true;
        try {
            const selectedKeys = this.getSelectedKeys(specificData);
            const attachmentId = await this.orm.call("report.custom_accounting_pro.engine", "export_excel", [
                this.state.reportType,
                this.state.dateFrom || null,
                this.state.dateTo || null,
                selectedKeys
            ]);
            this.actionService.doAction({
                type: 'ir.actions.act_url',
                url: `/web/content/ir.attachment/${attachmentId}/datas?download=true`,
                target: 'self'
            });
        } catch (e) {
            console.error("Export failed", e);
        } finally {
            this.state.loading = false;
        }
    }

    switchReport(type) {
        this.state.reportType = type;
        this.loadData();
    }

    async printPdf(specificData = null) {
        this.state.loading = true;
        try {
            const selectedKeys = this.getSelectedKeys(specificData);
            const attachmentId = await this.orm.call("report.custom_accounting_pro.engine", "export_pdf", [
                this.state.reportType,
                this.state.dateFrom || null,
                this.state.dateTo || null,
                selectedKeys
            ]);
            this.actionService.doAction({
                type: 'ir.actions.act_url',
                url: `/web/content/ir.attachment/${attachmentId}/datas?download=true`,
                target: 'self'
            });
        } catch (e) {
            console.error("PDF Export failed", e);
        } finally {
            this.state.loading = false;
        }
    }

    toggleExpand(accountId) {
        this.state.expanded[accountId] = !this.state.expanded[accountId];
    }

    toggleAll(ev) {
        const checked = ev.target.checked;
        for (const item of this.state.data) {
            const key = item.account_id || item.partner_id;
            this.state.selected[key] = checked;
        }
    }

    isAllSelected() {
        if (!this.state.data.length) return false;
        return this.state.data.every(item => this.state.selected[item.account_id || item.partner_id]);
    }
    
    formatCurrency(value) {
        if (!value) return "0.00";
        return parseFloat(value).toFixed(2).replace(/\d(?=(\d{3})+\.)/g, '$&,');
    }
}

DynamicAccountingReport.template = "custom_accounting_pro.DynamicReport";
registry.category("actions").add("dynamic_accounting_report", DynamicAccountingReport);
