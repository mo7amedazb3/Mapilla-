/** @odoo-module **/
import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { ListRenderer } from '@web/views/list/list_renderer';
import { X2ManyField, x2ManyField } from '@web/views/fields/x2many/x2many_field';

class DeliveryLinesRenderer extends ListRenderer {
    static template = 'furniture_delivery_requests.DeliveryLinesRenderer';
    static rowsTemplate = 'furniture_delivery_requests.DeliverySortedRows';
    sortedDeliveryRows(records) {
        const rank = record => {
            const name = (record.data.product_id?.[1] || '').replaceAll('ة', 'ه').replace(/[\u064b-\u065f\u0640]/g, '');
            return name.includes('كنبه') && name.includes('كبير') ? 0 : /فوتيه|فوتي|فوتوه/.test(name) ? 2 : 1;
        };
        return [...records].sort((a, b) => rank(a) - rank(b));
    }

    get cushionGroups() {
        const groups = new Map();
        for (const record of this.props.list.records) {
            const model = record.data.furniture_model_id;
            const key = model?.[0] || 0;
            for (const line of (record.data.takawe_summary || '').split('\n')) {
                if (!line.trim() || line.trim() === 'غير محدد') continue;
                const parts = line.split(' — ');
                const name = parts[0].trim();
                const size = parts[2] && /^\d+(?:[×x]\d+)?$/.test(parts[2].trim()) ? parts[2].trim() : '';
                const countPart = parts.find(part => /^\d+\s+تكوة$/.test(part.trim()));
                const count = (countPart ? Number.parseInt(countPart, 10) : 1) * (Number(record.data.quantity) || 0);
                if (!groups.has(key)) groups.set(key, {key, model: model?.[1] || '', items: new Map()});
                const items = groups.get(key).items;
                const itemKey = JSON.stringify([name, size]);
                if (!items.has(itemKey)) items.set(itemKey, {key: itemKey, name, size, count: 0});
                items.get(itemKey).count += count;
            }
        }
        return [...groups.values()].map(group => ({...group, items: [...group.items.values()]}));
    }

    get savedNotes() {
        const notes = this.props.list.model.root.data.notes || '';
        return notes.split('\n')
            .map(note => note.trim())
            .filter(Boolean)
            .map((text, index) => ({key: `${index}-${text}`, index, text}));
    }

    get canDeleteNotes() {
        return this.props.list.model.root.data.state === 'pending';
    }

    setup() {
        super.setup();
        this.deliveryAction = useService('action');
        this.deliveryOrm = useService('orm');
    }
    async deleteNote(note, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const root = this.props.list.model.root;
        if (!this.canDeleteNotes || !root.resId || this.deletingNote) return;
        this.deletingNote = note.key;
        try {
            if (await root.isDirty() && !await root.save()) return;
            await this.deliveryOrm.call(
                'furniture.mrp.future.order',
                'action_delete_note',
                [[root.resId], note.index, note.text],
            );
            await root.load();
        } finally {
            this.deletingNote = false;
        }
    }
    async onCellClicked(record, column, ev) {
        const root = this.props.list.model.root;
        const modelId = record.data.furniture_model_id?.[0];
        if (!modelId || !root.resId || root.data.state !== 'pending' || ev.target.special_click || column.widget === 'handle') {
            return super.onCellClicked(record, column, ev);
        }
        if (this.openingDelivery) return;
        this.openingDelivery = true;
        try {
            if (await root.isDirty() && !await root.save()) return;
            const action = await this.deliveryOrm.call('furniture.mrp.future.order', 'action_edit_products_popup', [[root.resId], modelId]);
            await this.deliveryAction.doAction(action, { onClose: () => root.load() });
        } finally { this.openingDelivery = false; }
    }
}
class DeliveryLinesField extends X2ManyField {
    static components = { ...X2ManyField.components, ListRenderer: DeliveryLinesRenderer };
}
registry.category('fields').add('delivery_editable_lines', { ...x2ManyField, component: DeliveryLinesField });
