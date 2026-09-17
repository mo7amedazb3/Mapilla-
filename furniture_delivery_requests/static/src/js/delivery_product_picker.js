/** @odoo-module **/
import { Component, onWillStart, useState } from '@odoo/owl';
import { Dialog } from '@web/core/dialog/dialog';
import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { standardWidgetProps } from '@web/views/widgets/standard_widget_props';

export class DeliveryProductPicker extends Component {
    static template = 'furniture_delivery_requests.ProductPicker';
    static components = { Dialog };
    static props = { close: Function, record: Object };
    setup() {
        this.orm = useService('orm');
        this.state = useState({ models: [], products: [], modelId: false, selected: {}, search: '', busy: false, error: '' });
        onWillStart(async () => {
            try {
                const catalog = await this.orm.call('furniture.mrp.future.order', 'get_product_picker_catalog', [this.props.record.data.company_id?.[0] || false]);
                Object.assign(this.state, catalog);
            } catch { this.state.error = 'تعذّر تحميل المنتجات. اقفل النافذة وجرّب مرة أخرى.'; }
        });
    }
    get products() {
        return this.state.products.filter(p => p.model_ids.includes(this.state.modelId) && p.name.includes(this.state.search.trim()));
    }
    get selectedProducts() { return this.state.products.filter(p => Object.hasOwn(this.state.selected, p.id)); }
    chooseModel(id) {
        if (this.state.busy || id === this.state.modelId) return;
        this.state.modelId = id; this.state.selected = {}; this.state.search = ''; this.state.error = '';
    }
    toggle(id) {
        if (this.state.busy) return;
        if (Object.hasOwn(this.state.selected, id)) delete this.state.selected[id];
        else this.state.selected[id] = 1;
    }
    async add() {
        if (this.state.busy || !this.selectedProducts.length) return;
        if (this.selectedProducts.some(p => !Number.isFinite(Number(this.state.selected[p.id])) || Number(this.state.selected[p.id]) <= 0)) {
            this.state.error = 'اكتب كمية أكبر من صفر لكل صنف.'; return;
        }
        this.state.busy = true; this.state.error = '';
        const list = this.props.record.data.line_ids;
        const added = [];
        try {
            if (!await list.leaveEditMode()) { this.state.error = 'استكمل بيانات السطر المفتوح أولًا.'; return; }
            for (const product of this.selectedProducts) {
                const row = await list.addNewRecord({ position: 'bottom', context: { default_product_id: product.id, default_quantity: Number(this.state.selected[product.id]) } });
                added.push(row);
                const model = this.state.models.find(m => m.id === this.state.modelId);
                await row.update({ furniture_model_id: [model.id, model.name], quantity: Number(this.state.selected[product.id]) });
            }
            this.props.close();
        } catch {
            for (const row of added) await list.delete(row);
            this.state.error = 'لم تتم إضافة الأصناف. راجع بيانات الطلب وحاول مرة أخرى.';
        } finally { this.state.busy = false; }
    }
}

class DeliveryAddProducts extends Component {
    static template = 'furniture_delivery_requests.AddProducts';
    static props = { ...standardWidgetProps };
    setup() { this.dialog = useService('dialog'); }
    open() { this.dialog.add(DeliveryProductPicker, { record: this.props.record }); }
}
registry.category('view_widgets').add('delivery_add_products', { component: DeliveryAddProducts });
