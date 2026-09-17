/** @odoo-module **/

import { Domain } from "@web/core/domain";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import {
    MonetaryField,
    monetaryField,
} from "@web/views/fields/monetary/monetary_field";
import { useState } from "@odoo/owl";

const { DateTime } = luxon;

const PAYROLL_PAY_BASIS_ACTIONS = Object.freeze({
    time: "furniture_mrp.action_furniture_payroll_time_slips",
    production: "furniture_mrp.action_furniture_payroll_production_slips",
});

export class FurniturePayrollRoundedMonetaryField extends MonetaryField {
    get currencyDigits() {
        return [16, 0];
    }
}

export const furniturePayrollRoundedMonetaryField = {
    ...monetaryField,
    component: FurniturePayrollRoundedMonetaryField,
};

registry.category("fields").add(
    "furniture_payroll_rounded_monetary",
    furniturePayrollRoundedMonetaryField
);

export function getCurrentFactoryPayrollWeek(today) {
    // Luxon uses Monday=1 ... Sunday=7.  Shifting by one makes Saturday
    // the first day of the payroll week and Friday its last day.
    const daysSinceSaturday = (today.weekday + 1) % 7;
    const weekStart = today.minus({ days: daysSinceSaturday });
    return {
        dateFrom: weekStart.toISODate(),
        dateTo: weekStart.plus({ days: 6 }).toISODate(),
    };
}

function getPayrollPeriod(context = {}) {
    const today = DateTime.now().startOf("day");
    const currentWeek = getCurrentFactoryPayrollWeek(today);
    // A Wednesday report must not pretend that Friday has already happened.
    // Keeping the full week end in the date input while refusing to load it
    // left the previous payroll rows on screen under the new dates, which is
    // dangerously misleading on a payroll report.  Thursday is the only
    // exception because Friday is the factory's weekly rest day.
    const allowedDateTo =
        today.weekday === 4 ? currentWeek.dateTo : today.toISODate();
    return {
        dateFrom:
            context.furniture_payroll_date_from ||
            currentWeek.dateFrom,
        dateTo: context.furniture_payroll_date_to || allowedDateTo,
        today: today.toISODate(),
        weekEnd: currentWeek.dateTo,
        allowedDateTo,
    };
}

function getPayrollPeriodDomain(context = {}) {
    const period = getPayrollPeriod(context);
    return [
        ["date_from", "=", period.dateFrom],
        ["date_to", "=", period.dateTo],
        ["state", "!=", "cancel"],
    ];
}

function getPayrollPayBasis(context = {}) {
    return context.furniture_payroll_pay_basis === "production"
        ? "production"
        : "time";
}

export class FurniturePayrollPeriodListController extends ListController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        const period = getPayrollPeriod(this.props.context);
        this.payBasis = useState({
            value: getPayrollPayBasis(this.props.context),
        });
        this.period = useState({
            ...period,
            loading: false,
            posting: false,
            paying: false,
            switching: false,
            openingWhatsApp: false,
        });
    }

    get canUsePeriod() {
        return Boolean(
            !this.period.loading &&
            !this.period.posting &&
            !this.period.paying &&
            !this.period.switching &&
            this.period.dateFrom &&
            this.period.dateTo &&
            this.period.dateFrom <= this.period.dateTo &&
            this.period.dateTo <= this.period.allowedDateTo
        );
    }

    async onPayrollPayBasisChange(payBasis) {
        if (
            this.period.switching ||
            payBasis === this.payBasis.value ||
            !PAYROLL_PAY_BASIS_ACTIONS[payBasis]
        ) {
            return;
        }
        this.period.switching = true;
        try {
            await this.actionService.doAction(
                PAYROLL_PAY_BASIS_ACTIONS[payBasis],
                {
                    additionalContext: {
                        furniture_payroll_date_from: this.period.dateFrom,
                        furniture_payroll_date_to: this.period.dateTo,
                    },
                    stackPosition: "replaceCurrentAction",
                }
            );
        } finally {
            this.period.switching = false;
        }
    }

    async onPayrollPeriodChange() {
        if (!this.canUsePeriod) {
            return;
        }
        this.period.loading = true;
        try {
            const action = await this.orm.call(
                "furniture.mrp.payroll.batch.wizard",
                "action_generate_period_slips",
                [this.period.dateFrom, this.period.dateTo],
                { context: this.props.context }
            );
            await this.actionService.doAction(action, {
                stackPosition: "replaceCurrentAction",
            });
        } finally {
            this.period.loading = false;
        }
    }

    async applyPayrollAccounting() {
        if (!this.canUsePeriod) {
            return;
        }
        this.period.posting = true;
        try {
            const action = await this.orm.call(
                "furniture.mrp.payroll.batch.wizard",
                "action_apply_period_accounting",
                [this.period.dateFrom, this.period.dateTo],
                { context: this.props.context }
            );
            await this.actionService.doAction(action);
        } finally {
            this.period.posting = false;
        }
    }

    async openPayrollPayment() {
        if (!this.canUsePeriod) {
            return;
        }
        this.period.paying = true;
        try {
            const action = await this.orm.call(
                "furniture.mrp.payroll.batch.wizard",
                "action_open_period_payment",
                [this.period.dateFrom, this.period.dateTo],
                { context: this.props.context }
            );
            await this.actionService.doAction(action);
        } finally {
            this.period.paying = false;
        }
    }

    async openPayrollWhatsAppWizard() {
        if (!this.hasSelectedRecords || this.period.openingWhatsApp) {
            return;
        }
        this.period.openingWhatsApp = true;
        try {
            const ids = await this.getSelectedResIds();
            const action = await this.orm.call(
                "simple.payroll.slip",
                "action_open_payroll_whatsapp_wizard",
                [ids],
                { context: this.props.context }
            );
            await this.actionService.doAction(action);
        } finally {
            this.period.openingWhatsApp = false;
        }
    }
}

export const furniturePayrollPeriodListView = {
    ...listView,
    Controller: FurniturePayrollPeriodListController,
    buttonTemplate: "furniture_mrp.PayrollPeriodList.Buttons",
    props: (genericProps, view) => {
        const props = listView.props(genericProps, view);
        return {
            ...props,
            domain: Domain.and([
                props.domain || [],
                getPayrollPeriodDomain(props.context),
            ]).toList(),
        };
    },
};

registry.category("views").add(
    "furniture_payroll_period_list",
    furniturePayrollPeriodListView
);
