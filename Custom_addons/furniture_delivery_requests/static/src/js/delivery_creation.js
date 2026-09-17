/** @odoo-module **/
import { registry } from '@web/core/registry';
import { ListController } from '@web/views/list/list_controller';
import { ListRenderer } from '@web/views/list/list_renderer';
import { listView } from '@web/views/list/list_view';
import { FormController } from '@web/views/form/form_controller';
import { formView } from '@web/views/form/form_view';

class DeliveryQueueRenderer extends ListRenderer {
    getRowClass(record) {
        return super.getRowClass(record) + (record.data.next_week_label ? ' mp_delivery_next_week' : '');
    }
}

const creationAction = 'furniture_delivery_requests.action_delivery_creation_wizard';
class DeliveryListController extends ListController {
    static template = 'furniture_delivery_requests.DeliveryList';
    async createRecord() {
        return this.actionService.doAction(creationAction, { onClose: () => this.model.load() });
    }
}
class DeliveryFormController extends FormController {
    async create() {
        if (await this.model.root.isDirty() && !await this.model.root.save({ onError: this.onSaveError.bind(this) })) return;
        return this.actionService.doAction(creationAction);
    }
}
registry.category('views').add('delivery_creation_list', { ...listView, Controller: DeliveryListController, Renderer: DeliveryQueueRenderer });
registry.category('views').add('delivery_creation_form', { ...formView, Controller: DeliveryFormController });

class CompanyStandardsFormController extends FormController {
    async create() {
        if (!this.model.root.resId) return;
        const action = await this.orm.call('res.partner', 'action_delivery_preferences', [[this.model.root.resId]], {
            context: { ...this.props.context, company_standards_page: true },
        });
        return this.actionService.doAction(action);
    }
}
registry.category('views').add('company_standards_form', { ...formView, Controller: CompanyStandardsFormController });
