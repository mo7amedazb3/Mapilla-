/** @odoo-module **/
import { Component, xml, useState, useEffect, useSubEnv, onMounted } from '@odoo/owl';
import { patch } from '@web/core/utils/patch';
import { registry } from '@web/core/registry';
import { standardFieldProps } from '@web/views/fields/standard_field_props';
import { FurnitureQuickCardSelector } from '@furniture_mrp/js/furniture_quick_card_selector';
import { View } from '@web/views/view';
import { FormController } from '@web/views/form/form_controller';
import { formView } from '@web/views/form/form_view';
import { FormViewDialog } from '@web/views/view_dialogs/form_view_dialog';

class DeliverySpecificationDialog extends FormViewDialog {
    setup() {
        super.setup();
        this.viewProps.buttonTemplate = 'furniture_delivery_requests.SpecificationButtons';
    }
}

class DeliverySpecificationsButton extends Component {
    static props = { ...standardFieldProps };
    static template = xml`<button type="button" class="btn btn-secondary o_furniture_dimension_toggle o_delivery_specifications_button" t-att-disabled="state.busy" t-on-click.stop="open"><i class="fa fa-sliders me-1"/>تعديل التفاصيل</button>`;
    setup() {
        // Saving an unsaved parent can replace this list-cell component.
        // Keep the services alive until the child dialog finishes.
        this.orm = this.env.services.orm;
        this.dialog = this.env.services.dialog;
        this.state = useState({ busy: false });
    }
    async open() {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            const record = this.props.record;
            const root = record.model.root;
            const isProfile = ['furniture.delivery.customer.spec', 'furniture.delivery.customer.editor'].includes(record.resModel);
            if (isProfile) {
                const values = {};
                for (const key of ['partner_id', 'company_id', 'model_id', 'product_id']) {
                    values[key] = record.data[key]?.[0] || false;
                }
                for (const key of ['width_cm', 'depth_cm', 'height_cm', 'upholstery_data']) {
                    values[key] = record.data[key];
                }
                if (!values.model_id || !values.product_id) return;
                const action = await this.orm.call('furniture.delivery.customer.spec', 'action_edit_draft_specification', [values]);
                this.dialog.add(DeliverySpecificationDialog, {
                    resModel: action.res_model, resId: action.res_id, context: action.context || {},
                    viewId: action.views[0][0], title: action.name, size: 'xl',
                    onRecordSaved: async (editor) => {
                        await this.orm.call('furniture.delivery.spec.wizard', 'action_apply', [[editor.resId]]);
                        const [data] = await this.orm.read('furniture.delivery.creation.line', [action.draft_line_id],
                            ['width_cm', 'depth_cm', 'height_cm', 'upholstery_data']);
                        delete data.id;
                        await record.update(data);
                    },
                });
                return;
            }
            const fieldName = 'line_ids';
            const targetModel = 'furniture.delivery.creation.line';
            const index = root.data[fieldName].records.indexOf(record);
            const oldId = record.resId;
            if (!await root.save()) return;
            const line = root.data[fieldName].records.find(r => oldId && r.resId === oldId)
                || root.data[fieldName].records[index];
            if (!line?.resId) return;
            const action = await this.orm.call(targetModel, 'action_open_specifications', [[line.resId]]);
            this.dialog.add(DeliverySpecificationDialog, {
                resModel: action.res_model, resId: action.res_id,
                viewId: action.views[0][0], title: action.name, size: 'xl',
                onRecordSaved: async (editor) => {
                    await this.orm.call('furniture.delivery.spec.wizard', 'action_apply', [[editor.resId]]);
                    const [values] = await this.orm.read(targetModel, [line.resId],
                        ['width_cm', 'depth_cm', 'height_cm', 'upholstery_data',
                         'dimension_label', 'fabric_summary', 'takawe_summary']);
                    delete values.id;
                    await line.update(values);
                },
            });
        } finally {
            this.state.busy = false;
        }
    }
}
registry.category('fields').add('delivery_specifications_button', {
    component: DeliverySpecificationsButton, supportedTypes: ['boolean'],
});

class DeliveryTakaweCounts extends Component {
    static props = { ...standardFieldProps };
    static template = xml`<div class="o_delivery_takawe_counts">
        <div t-foreach="items" t-as="item" t-key="item.index" class="mb-1">
            <small t-if="items.length > 1" t-esc="item.name"/>
            <input type="number" min="1" step="1" class="o_input" style="width:90px" aria-label="عدد التكاوي"
                t-att-value="item.count" t-att-disabled="props.readonly" t-on-input="(ev) => this.change(ev, item.index)"/>
        </div><span t-if="!items.length">—</span>
    </div>`;
    get items() {
        const names = (this.props.record.data.takawe_summary || '').split('\n').map(v => v.split(' — ')[0]);
        let n = 0;
        return (this.props.record.data[this.props.name] || []).flatMap((v, index) =>
            v.kind === 'takawe' ? [{ index, count: v.piece_count || 1, name: names[n++] || '' }] : []);
    }
    async change(ev, index) {
        const count = Number(ev.target.value);
        const data = JSON.parse(JSON.stringify(this.props.record.data[this.props.name] || []));
        if (!Number.isInteger(count) || count < 1) { ev.target.value = data[index].piece_count || 1; return; }
        data[index].piece_count = count;
        await this.props.record.update({ [this.props.name]: data });
    }
}
registry.category('fields').add('delivery_takawe_counts', {
    component: DeliveryTakaweCounts, supportedTypes: ['json'],
});

class DeliveryDimensionSummary extends Component {
    static props = {...standardFieldProps};
    static template = xml`<span t-if="props.record.data[props.name]" class="o_delivery_dimension_badge"><bdi dir="ltr" t-esc="props.record.data[props.name]"/><small>سم</small></span>`;
}
registry.category('fields').add('delivery_dimension_summary', {component: DeliveryDimensionSummary, supportedTypes: ['char']});

class DeliveryMaterialSummary extends Component {
    static props = { ...standardFieldProps, namesOnly: {type: Boolean, optional: true} };
    static template = xml`<div class="o_delivery_material_summary">
        <t t-if="items.length"><div t-foreach="items" t-as="item" t-key="item_index" class="o_delivery_material_item">
            <strong t-att-title="item.name" t-esc="item.name"/>
            <div t-if="!props.namesOnly" class="o_delivery_material_meta"><span t-if="item.quantity"><bdi t-esc="item.quantity"/><t t-if="item.count"> / تكوة</t><t t-else=""> / قطعة</t></span><span t-if="item.count" t-esc="item.count"/><span t-if="item.total">الإجمالي <bdi t-esc="item.total"/> / صنف</span><span t-if="item.size">المقاس <bdi t-esc="item.size"/></span></div>
        </div></t><span t-else="" class="o_delivery_material_empty">لم يُحدّد</span>
    </div>`;
    get items() {
        const value = this.props.record.data[this.props.name];
        if (!value || value === 'غير محدد') return [];
        return value.split('\n').filter(Boolean).map(line => {
            const [name, quantity, size, count, total] = line.split(' — ');
            return { name, quantity, size, count, total };
        });
    }
}
registry.category('fields').add('delivery_material_summary', {
    component: DeliveryMaterialSummary, supportedTypes: ['text'],
    extractProps: ({options}) => ({namesOnly: Boolean(options.names_only)}),
});

class DeliveryItemPreview extends Component {
    static props = { ...standardFieldProps };
    static template = xml`<div class="mp_delivery_items" dir="rtl">
        <span t-foreach="props.record.data[props.name] || []" t-as="item" t-key="item_index" class="mp_delivery_item">
            <span t-esc="item.name"/><bdi class="mp_delivery_item_qty" t-esc="item.quantity"/>
        </span>
    </div>`;
}
registry.category('fields').add('delivery_item_preview', {
    component: DeliveryItemPreview, supportedTypes: ['json'],
});

// Customer product choices depend on the currently selected furniture model.
class CustomerProductSelector extends FurnitureQuickCardSelector {
    setup() {
        super.setup();
        useEffect(() => {
            if (JSON.stringify(this.getDomain()) !== this.loadedDomain) {
                this.loadItems();
            }
        }, () => [JSON.stringify(this.getDomain())]);
    }
    async loadItems(props = this.props) {
        const domain = this.getDomainForProps(props);
        const request = this.requestNumber = (this.requestNumber || 0) + 1;
        this.loadedDomain = JSON.stringify(domain);
        this.domainKey = this.loadedDomain;
        this.cardState.items = [];
        this.cardState.loading = true;
        this.cardState.loadFailed = false;
        try {
            const relation = props.record.fields[props.name].relation;
            const records = await this.orm.searchRead(relation, domain, ['name', 'display_name'], {
                context: props.context, order: props.orderBy || 'name, id',
            });
            if (request !== this.requestNumber) return;
            this.cardState.items = this.sortItems(records.map(r => ({
                id: r.id, name: r.name || r.display_name, displayName: r.display_name || r.name,
            })), props.quickNames || []);
        } catch (error) {
            if (request === this.requestNumber) this.cardState.loadFailed = true;
        } finally {
            if (request === this.requestNumber) this.cardState.loading = false;
        }
    }
}
registry.category('fields').add('delivery_customer_product_selector', {
    ...registry.category('fields').get('furniture_quick_card_selector'),
    component: CustomerProductSelector,
});

// Keep the established widget name usable by already-open web clients.
patch(FurnitureQuickCardSelector.prototype, {
    async selectItem(item) {
        const editorField = ['furniture.delivery.customer.editor', 'furniture.delivery.creation.wizard'].includes(this.props.record.resModel)
            && ['model_id', 'product_id'].includes(this.props.name);
        if (editorField && this.isSelected(item.id)) {
            if (this.cardState.selectingId) return;
            this.cardState.selectingId = item.id;
            try {
                await this.props.record.update({ [this.props.name]: false });
                this.cardState.expanded = false;
            } finally {
                this.cardState.selectingId = false;
            }
            return;
        }
        return super.selectItem(item);
    },
    setup() {
        super.setup();
        if (this.props.record.resModel === 'furniture.delivery.customer.editor' && this.props.name === 'product_id') {
            useEffect(() => {
                if (JSON.stringify(this.getDomain()) !== this.loadedDomain) this.loadItems();
            }, () => [JSON.stringify(this.getDomain())]);
        }
    },
    async loadItems(props = this.props) {
        if (this.props.record.resModel === 'furniture.delivery.customer.editor' && this.props.name === 'product_id') {
            return CustomerProductSelector.prototype.loadItems.call(this, props);
        }
        return super.loadItems(props);
    },
});

class CompanyStandardModels extends Component {
    static props = { ...standardFieldProps };
    static template = xml`<div class="o_company_standard_models_grid" dir="rtl">
        <button t-foreach="state.models" t-as="model" t-key="model.id" type="button"
            class="btn o_company_standard_model"
            t-att-disabled="state.busy" t-on-click="() => this.open(model.id)">
            <span class="o_company_model_art" aria-hidden="true">
                <svg viewBox="0 0 96 72" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M22 38V24a9 9 0 0 1 9-9h34a9 9 0 0 1 9 9v14" fill="currentColor" opacity=".12"/>
                    <path d="M22 38V24a9 9 0 0 1 9-9h34a9 9 0 0 1 9 9v14M48 17v21" stroke="currentColor" stroke-width="3"/>
                    <path d="M15 35a6 6 0 0 1 6 6v3h54v-3a6 6 0 0 1 12 0v14H9V41a6 6 0 0 1 6-6Z" fill="white" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>
                    <path d="M19 56v7m58-7v7" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>
                </svg>
            </span>
            <span class="o_company_model_caption"><strong t-esc="model.name"/><span>الأقمشة والتكاوي</span></span>
            <span class="o_company_model_arrow" aria-hidden="true"><i class="fa fa-angle-left"/></span>
        </button>
        <span t-if="!state.loading and !state.models.length" class="text-muted">لا توجد اختيارات محفوظة بعد. اضغط New لإضافتها.</span>
    </div>`;
    setup() {
        this.state = useState({ models: [], busy: false, loading: true });
        this.orm = this.env.services.orm;
        this.action = this.env.services.action;
        useEffect(() => { this.load(); }, () => [this.props.record.resId]);
    }
    async load() {
        try {
            this.state.models = this.props.record.resId ? await this.orm.call('res.partner',
                'delivery_standard_models', [[this.props.record.resId]], { context: this.props.record.context }) : [];
        } finally { this.state.loading = false; }
    }
    async open(modelId) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            const action = await this.orm.call('res.partner', 'action_delivery_model_preferences',
                [[this.props.record.resId], modelId], { context: this.props.record.context });
            await this.action.doAction(action, { onClose: () => this.load() });
        } finally { this.state.busy = false; }
    }
}
registry.category('fields').add('company_standard_models', {
    component: CompanyStandardModels, supportedTypes: ['one2many'],
});

class CompanyDetailProductPicker extends Component {
    static props = { ...standardFieldProps };
    static template = xml`<select t-if="env.companyDetailNavigation" class="o_company_detail_product_picker form-select"
        aria-label="اختيار الصنف من نفس الموديل" t-att-value="props.record.data[props.name]?.[0]"
        t-att-disabled="state.busy or !state.ready"
        t-on-change="change">
        <option t-foreach="env.companyDetailNavigation.products()" t-as="product" t-key="product.id" t-att-value="product.id" t-esc="product.name"/>
    </select><span t-else="" t-esc="props.record.data[props.name]?.[1] || ''"/>`;
    setup() {
        this.state = useState(this.env.companyDetailNavigation?.state || { busy: false, ready: true });
    }
    async change(event) {
        const target = event.target;
        const id = Number(target.value);
        target.value = String(this.props.record.data[this.props.name]?.[0] || '');
        await this.env.companyDetailNavigation.switchProduct(id);
    }
}
registry.category('fields').add('company_detail_product_picker', {
    component: CompanyDetailProductPicker, supportedTypes: ['many2one'],
});

class CompanyDetailModelPicker extends CompanyDetailProductPicker {
    static template = xml`<select t-if="env.companyDetailNavigation" class="o_company_detail_product_picker o_company_detail_model_picker form-select"
        aria-label="اختيار الموديل" t-att-value="props.record.data[props.name]?.[0]"
        t-att-disabled="state.busy or !state.ready" t-on-change="change">
        <option t-foreach="env.companyDetailNavigation.models()" t-as="model" t-key="model.id" t-att-value="model.id" t-esc="model.name"/>
    </select><span t-else="" t-esc="props.record.data[props.name]?.[1] || ''"/>`;
    async change(event) {
        const id = Number(event.target.value);
        event.target.value = String(this.props.record.data[this.props.name]?.[0] || '');
        await this.env.companyDetailNavigation.switchModel(id);
    }
}
registry.category('fields').add('company_detail_model_picker', {
    component: CompanyDetailModelPicker, supportedTypes: ['many2one'],
});

class CompanyInlineDetailsController extends FormController {
    setup() {
        super.setup();
        onMounted(() => this.env.companyDetailsReady(this.model.root));
    }
}
registry.category('views').add('company_inline_details', { ...formView, Controller: CompanyInlineDetailsController });

class CompanyInlineDetails extends Component {
    static components = { View };
    static props = {
        action: Object,
        buyerName: String,
        onBack: Function,
        products: Array,
        models: Array,
        onModelSwitch: Function,
        onSwitch: Function,
        onNext: { type: Function, optional: true },
    };
    static template = xml`<section class="o_company_inline_details" t-att-class="{'is-leaving': state.leaving, 'is-advancing': state.advancing}">
        <div class="o_company_detail_navigation" dir="ltr">
            <div class="o_company_detail_actions">
                <button type="button" class="btn btn-outline-primary o_company_detail_back" t-att-disabled="state.busy or !state.ready" t-on-click="back">
                    <i class="fa fa-chevron-left me-2"/>رجوع
                </button>
                <button t-if="props.onNext" type="button" class="btn btn-primary o_company_detail_next" t-att-disabled="state.busy or !state.ready" t-on-click="next">
                    <t t-esc="nextProduct ? 'التالي' : 'إنهاء'"/><i class="fa fa-chevron-right ms-2"/>
                </button>
                <span t-if="props.onNext and state.ready" class="o_company_detail_step" dir="rtl" t-esc="stepLabel"/>
            </div>
            <strong dir="rtl">تعديل تفاصيل الصنف</strong>
        </div>
        <View t-props="viewProps"/>
    </section>`;
    setup() {
        this.state = useState({ busy: false, ready: false, leaving: false, advancing: false });
        useSubEnv({ inDialog: false, companyDetailNavigation: {
            state: this.state, products: () => this.props.products,
            switchProduct: id => this.switchProduct(id),
            models: () => this.props.models, switchModel: id => this.switchModel(id),
        }, companyDetailsReady: record => {
            this.record = record; this.state.ready = true;
        } });
        this.viewProps = {
            type: 'form', jsClass: 'company_inline_details',
            resModel: this.props.action.res_model, resId: this.props.action.res_id,
            viewId: this.props.action.views[0][0], mode: 'edit',
            context: this.props.action.context || {}, display: { controlPanel: false },
            preventCreate: true,
        };
    }
    get currentProductIndex() {
        const id = this.record?.data.product_id?.[0];
        return this.props.products.findIndex(product => product.id === id);
    }
    get nextProduct() {
        const index = this.currentProductIndex;
        return index >= 0 ? this.props.products[index + 1] : undefined;
    }
    get stepLabel() {
        const index = this.currentProductIndex;
        return index >= 0 ? `${index + 1} من ${this.props.products.length}` : '';
    }
    async animateLeaving(advancing = false) {
        this.state.advancing = advancing;
        this.state.leaving = true;
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            await new Promise(resolve => setTimeout(resolve, 200));
        }
    }
    async switchModel(id) {
        if (this.state.busy || !this.record || id === this.record.data.model_id?.[0]) return;
        if (!this.props.models.some(model => model.id === id)) return;
        this.state.busy = true;
        try {
            if (!await this.record.save({ reload: false })) return;
            await this.props.onModelSwitch(this.record, id);
        } finally { this.state.busy = false; }
    }
    async switchProduct(id) {
        if (this.state.busy || !this.record || id === this.record.data.product_id?.[0]) return;
        if (!this.props.products.some(product => product.id === id)) return;
        this.state.busy = true;
        try {
            if (!await this.record.save({ reload: false })) return;
            await this.props.onSwitch(this.record, id);
        } finally { this.state.busy = false; }
    }
    async back() {
        if (this.state.busy || !this.record) return;
        this.state.busy = true;
        try {
            if (!await this.record.save({ reload: false })) return;
            await this.props.onBack(this.record, () => this.animateLeaving());
        } finally { this.state.busy = false; }
    }
    async next() {
        if (this.state.busy || !this.record || !this.props.onNext) return;
        this.state.busy = true;
        try {
            if (!await this.record.save({ reload: false })) return;
            await this.props.onNext(
                this.record,
                this.nextProduct?.id || false,
                () => this.animateLeaving(true),
            );
        } finally { this.state.busy = false; }
    }
}

class CompanyModelOverview extends Component {
    static components = { CompanyInlineDetails };
    static props = { ...standardFieldProps };
    static template = xml`<div class="o_company_model_overview" dir="rtl">
        <CompanyInlineDetails t-if="state.details" t-key="state.details.res_id" action="state.details" buyerName="state.buyerName" products="state.rows" models="state.models" onModelSwitch.bind="switchModelDetails" onBack.bind="backFromDetails" onSwitch.bind="switchDetails"/>
        <div t-else="" class="o_company_overview_page">
        <div class="o_company_overview_controls">
        <div class="o_company_product_cards">
            <button t-foreach="state.rows" t-as="row" t-key="row.id" type="button"
                t-att-class="selected(row.id) ? 'btn btn-primary' : 'btn btn-outline-primary'"
                t-on-click="() => this.toggle(row.id)">
                <i t-att-class="selected(row.id) ? 'fa fa-check-circle ms-2' : 'fa fa-cube ms-2'"/><t t-esc="row.name"/>
            </button>
        </div>
        <div class="o_company_details_toolbar" t-if="state.rows.length"><button type="button" class="btn btn-primary o_company_edit_model_details" t-att-disabled="state.busy" t-on-click="editModel"><i class="fa fa-sliders ms-2"/>تعديل التفاصيل</button></div>
        </div>
        <div class="table-responsive"><table class="o_company_specs_table" t-if="state.rows.length">
            <thead><tr><th>الصنف</th><th>القماش</th><th>التكاوي</th></tr></thead>
            <tbody><t t-foreach="state.rows" t-as="row" t-key="row.id"><tr t-if="selected(row.id)">
                <td><strong t-esc="row.name"/></td>
                <td class="material"><div t-foreach="materials(row.fabric)" t-as="item" t-key="item_index"><strong t-att-title="item.name" t-esc="item.name"/></div></td>
                <td class="material"><div t-foreach="materials(row.takawe)" t-as="item" t-key="item_index"><strong t-att-title="item.name" t-esc="item.name"/></div></td>
            </tr></t></tbody>
        </table></div>
        </div>
    </div>`;
    setup() {
        this.state = useState({ rows: [], models: [], buyerName: '', excluded: {}, busy: false, details: null });
        this.orm = this.env.services.orm;
        this.dialog = this.env.services.dialog;
        useEffect(() => { this.load(); }, () => {
            const d = this.props.record.data;
            return [d.model_id?.[0], JSON.stringify(d.draft_data), d.current_key,
                d.width_cm, d.depth_cm, d.height_cm, JSON.stringify(d.upholstery_data)];
        });
    }
    materials(value) {
        return (value || 'غير محدد').split('\n').map(line => {
            const [name, quantity, size, count, total] = line.split(' — ');
            return { name, detail: [quantity ? `${quantity} / ${count ? 'تكوة' : 'قطعة'}` : '', size ? `المقاس ${size}` : '', count ? `العدد: ${count}` : ''].filter(Boolean).join(' · ') };
        });
    }
    key(id) { return `${this.props.record.data.model_id?.[0]}:${id}`; }
    selected(id) { return !this.state.excluded[this.key(id)]; }
    toggle(id) { this.state.excluded[this.key(id)] = this.selected(id); }
    async load() {
        const version = this.version = (this.version || 0) + 1;
        const record = this.props.record, d = record.data;
        if (!d.model_id) { this.state.rows = []; return; }
        const drafts = { ...(d.draft_data || {}) };
        if (d.current_key) drafts[d.current_key] = {
            width_cm: d.width_cm, depth_cm: d.depth_cm, height_cm: d.height_cm,
            upholstery_data: d.upholstery_data || [],
        };
        const rows = await this.orm.call(record.resModel, 'model_overview',
            [[record.resId], d.model_id[0], drafts], { context: record.context });
        if (version === this.version) this.state.rows = rows;
        return rows;
    }
    async editModel() {
        const current = this.props.record.data.product_id?.[0];
        const row = this.state.rows.find(row => row.id === current && this.selected(row.id))
            || this.state.rows.find(row => this.selected(row.id)) || this.state.rows[0];
        if (row) await this.edit(row.id);
    }
    async edit(id) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            if (!this.state.models.length) {
                const record = this.props.record;
                this.state.models = await this.orm.call(record.resModel, 'detail_models', [[record.resId]], { context: record.context });
            }
            if (!this.state.buyerName && this.props.record.data.partner_id?.[0]) {
                const [buyer] = await this.orm.read('res.partner', [this.props.record.data.partner_id[0]], ['name']);
                this.state.buyerName = buyer.name || '';
            }
            const row = this.state.rows.find(r => r.id === id);
            await this.props.record.update({ product_id: [id, row.name] });
            const record = this.props.record;
            const values = {};
            for (const key of ['partner_id', 'company_id', 'model_id', 'product_id']) {
                values[key] = record.data[key]?.[0] || false;
            }
            for (const key of ['width_cm', 'depth_cm', 'height_cm', 'upholstery_data']) {
                values[key] = record.data[key];
            }
            this.state.details = await this.orm.call('furniture.delivery.customer.spec', 'action_edit_draft_specification', [values]);
        } finally { this.state.busy = false; }
    }
    async applyDetailDraft(editor) {
        const action = this.state.details;
        await this.orm.call('furniture.delivery.spec.wizard', 'action_apply', [[editor.resId]]);
        const [data] = await this.orm.read('furniture.delivery.creation.line', [action.draft_line_id],
            ['width_cm', 'depth_cm', 'height_cm', 'upholstery_data']);
        delete data.id;
        await this.props.record.update(data);
        await this.load();
    }
    async switchModelDetails(editor, modelId) {
        await this.applyDetailDraft(editor);
        const model = this.state.models.find(m => m.id === modelId);
        const productId = editor.data.product_id?.[0];
        await this.props.record.update({ model_id: [model.id, model.name] });
        const rows = await this.load();
        this.state.rows = rows;
        const row = rows.find(r => r.id === productId) || rows[0];
        if (row) await this.edit(row.id);
        else this.state.details = null;
    }
    async switchDetails(editor, productId) {
        await this.applyDetailDraft(editor);
        await this.edit(productId);
    }
    async backFromDetails(editor, animate) {
        await this.applyDetailDraft(editor);
        await animate();
        this.state.details = null;
    }

}
registry.category('fields').add('company_model_overview', {
    component: CompanyModelOverview, supportedTypes: ['json'],
});

// The delivery draft uses the same inline navigation as company standards.
class DeliveryCreationOverview extends Component {
    static components = { CompanyInlineDetails };
    static props = { ...standardFieldProps };
    static template = xml`<div class="o_delivery_creation_overview" dir="rtl">
      <CompanyInlineDetails t-if="state.details" t-key="state.details.res_id" action="state.details" buyerName="''" products="detailProducts" models="state.models" onModelSwitch.bind="switchModel" onSwitch.bind="switchProduct" onBack.bind="back" onNext.bind="next"/>
      <div t-else="" class="o_company_overview_page">
        <div class="o_company_overview_controls">
          <div class="o_company_product_cards">
            <button t-foreach="products" t-as="product" t-key="product.id" type="button" t-att-disabled="state.busy" t-att-class="selected(product.id) ? 'btn btn-primary' : 'btn btn-outline-primary'" t-on-click="() => this.toggle(product.id)"><i t-att-class="selected(product.id) ? 'fa fa-check-circle ms-2' : 'fa fa-cube ms-2'"/><t t-esc="product.name"/></button>
          </div>
          <div class="o_company_details_toolbar" t-if="rows.length"><button type="button" class="btn btn-primary o_company_edit_model_details" t-att-disabled="state.busy" t-on-click="editFirst"><i class="fa fa-sliders ms-2"/>تعديل التفاصيل</button></div>
        </div>
        <div class="table-responsive"><table class="o_company_specs_table" t-if="rows.length">
          <thead><tr><th>الصنف</th><th>الكمية</th><th>القماش</th><th>المقاس</th></tr></thead>
          <tbody><tr t-foreach="rows" t-as="row" t-key="row.id">
            <td><strong t-esc="row.data.product_id[1]"/></td>
            <td><input class="o_input o_delivery_overview_quantity" type="number" min="0.01" step="any" aria-label="الكمية" t-att-value="row.data.quantity" t-on-change="ev => this.quantity(row, ev)"/></td>
            <td class="material"><div t-foreach="names(row.data.fabric_summary)" t-as="name" t-key="name_index"><strong t-esc="name"/></div></td>
            <td class="o_delivery_draft_dimensions"><span t-if="row.data.dimension_label" class="o_delivery_dimension_badge"><bdi dir="ltr" t-esc="row.data.dimension_label"/><small>سم</small></span></td>
          </tr></tbody>
        </table></div>
        <div t-if="cushions.length" class="o_delivery_draft_cushions">
          <strong class="o_delivery_draft_cushion_title">التكاوي</strong>
          <div class="o_delivery_draft_cushion_cards">
            <div t-foreach="cushions" t-as="item" t-key="item.key" class="o_delivery_draft_cushion_card">
              <strong t-att-title="item.name" t-esc="item.name"/>
              <div class="o_delivery_draft_cushion_numbers">
                <span t-att-title="'العدد: ' + item.count">العدد: <bdi t-esc="item.count"/></span>
                <bdi t-if="item.size" t-att-title="'المقاس: ' + item.size" t-esc="item.size"/>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>`;
    setup() {
        this.orm = this.env.services.orm;
        this.state = useState({ details: null, models: [], products: [], busy: false });
        useEffect(() => { this.loadCatalog(); }, () => [this.props.record.data.company_id?.[0]]);
    }
    async loadCatalog() {
        const company = this.props.record.data.company_id?.[0];
        const catalog = await this.orm.call('furniture.mrp.future.order', 'get_product_picker_catalog', [company]);
        if (company !== this.props.record.data.company_id?.[0]) return;
        this.state.models = catalog.models;
        this.state.products = catalog.products;
    }
    rank(name) {
        name = (name || '').replaceAll('ة', 'ه');
        return name.includes('كنبه') && name.includes('كبير') ? 0 : name.includes('فوتيه') ? 2 : 1;
    }
    get products() { return this.state.products.filter(p => !p.is_kit && p.model_ids.includes(this.props.record.data.model_id?.[0])).sort((a,b) => this.rank(a.name)-this.rank(b.name)); }
    get rows() { return [...this.props.record.data[this.props.name].records].sort((a,b) => this.rank(a.data.product_id?.[1])-this.rank(b.data.product_id?.[1])); }
    get cushions() {
        const items = new Map();
        for (const row of this.rows) {
            for (const line of (row.data.takawe_summary || '').split('\n')) {
                if (!line.trim() || line.trim() === 'غير محدد') continue;
                const parts = line.split(' — ');
                const name = parts[0].trim();
                const size = parts[2] && /^\d+(?:[×x]\d+)?$/.test(parts[2].trim()) ? parts[2].trim() : '';
                const countPart = parts.find(part => /^\d+\s+تكوة$/.test(part.trim()));
                const count = (countPart ? Number.parseInt(countPart, 10) : 1) * (Number(row.data.quantity) || 0);
                const key = JSON.stringify([name, size]);
                if (!items.has(key)) items.set(key, {key, name, size, count: 0});
                items.get(key).count += count;
            }
        }
        return [...items.values()];
    }
    get detailProducts() { return this.rows.map(row => ({ id: row.data.product_id[0], name: row.data.product_id[1] })); }
    selected(id) { return this.props.record.data.selected_product_ids.currentIds.includes(id); }
    names(value) { return (value || 'غير محدد').split('\n').map(line => line.split(' — ')[0]); }
    async toggle(id) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            const ids = this.props.record.data.selected_product_ids.currentIds;
            await this.props.record.update({ selected_product_ids: [[6, 0, this.selected(id) ? ids.filter(x => x !== id) : [...ids, id]]] });
        } finally { this.state.busy = false; }
    }
    async quantity(row, event) {
        const value = Number(event.target.value);
        if (!Number.isFinite(value) || value <= 0) { event.target.value = row.data.quantity; return; }
        await row.update({ quantity: value });
    }
    async editFirst() { if (this.rows.length) await this.edit(this.rows[0].data.product_id[0]); }
    async edit(productId) {
        const row = this.rows.find(r => r.data.product_id[0] === productId);
        if (!row) { this.state.details = null; return; }
        this.state.busy = true;
        try {
            const d = this.props.record.data;
            this.editingProduct = productId;
            this.state.details = await this.orm.call('furniture.delivery.creation.wizard', 'action_edit_inline_draft', [{
                company_id: d.company_id?.[0], buyer_partner_id: d.buyer_partner_id?.[0], model_id: d.model_id?.[0], product_id: productId,
                width_cm: row.data.width_cm, depth_cm: row.data.depth_cm, height_cm: row.data.height_cm,
                upholstery_data: JSON.parse(JSON.stringify(row.data.upholstery_data || [])),
            }]);
        } finally { this.state.busy = false; }
    }
    async apply(editor) {
        await this.orm.call('furniture.delivery.spec.wizard', 'action_apply', [[editor.resId]]);
        const [values] = await this.orm.read('furniture.delivery.creation.line', [this.state.details.draft_line_id], ['width_cm', 'depth_cm', 'height_cm', 'upholstery_data']);
        delete values.id;
        const row = this.rows.find(r => r.data.product_id[0] === this.editingProduct);
        if (row) await row.update(values);
    }
    async switchProduct(editor, id) { await this.apply(editor); await this.edit(id); }
    async next(editor, id, animate) {
        await this.apply(editor);
        await animate();
        if (id) await this.edit(id);
        else this.state.details = null;
    }
    async switchModel(editor, id) {
        await this.apply(editor);
        const model = this.state.models.find(m => m.id === id);
        await this.props.record.update({ model_id: [id, model.name] });
        if (this.rows.length) await this.editFirst();
        else this.state.details = null;
    }
    async back(editor, animate) { await this.apply(editor); await animate(); this.state.details = null; }
}
registry.category('fields').add('delivery_creation_overview', { component: DeliveryCreationOverview, supportedTypes: ['one2many'], useSubView: true });
