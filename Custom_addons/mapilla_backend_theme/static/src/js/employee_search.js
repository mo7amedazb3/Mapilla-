/** @odoo-module **/
import { SearchBar } from '@web/search/search_bar/search_bar';
import { patch } from '@web/core/utils/patch';
import { Domain } from '@web/core/domain';
import { useService } from '@web/core/utils/hooks';
import { onWillDestroy } from '@odoo/owl';

// Employee autocomplete shows matching names only. Other models keep standard search.
patch(SearchBar.prototype, {
    setup() {
        super.setup(...arguments);
        this.employeeAction = useService("action");
        this.employeeSearchGeneration = 0;
        onWillDestroy(() => { this.employeeSearchGeneration++; });
    },
    async computeState(options = {}) {
        if (this.env.searchModel.resModel !== 'hr.employee') {
            return super.computeState(options);
        }
        const generation = ++this.employeeSearchGeneration;
        this.state.query = options.query ?? this.state.query;
        this.state.expanded = [];
        this.state.focusedIndex = 0;
        this.subItems = {};
        this.inputRef.el.value = this.state.query;
        this.items.length = 0;
        const query = this.state.query.trim();
        if (!query || generation !== this.employeeSearchGeneration) return;
        // Debounce typing; outdated responses must never replace newer suggestions.
        await new Promise(resolve => setTimeout(resolve, 220));
        if (generation !== this.employeeSearchGeneration) return;
        let employees;
        try {
            employees = await this.orm.searchRead('hr.employee',
                Domain.and([this.env.searchModel.domain, [['name', 'ilike', query]]]).toList(),
                ['name'], { limit: 100, order: 'name, id', context: this.env.searchModel.context });
        } catch {
            // Do not show unrelated filter choices if the request fails.
            return;
        }
        if (generation !== this.employeeSearchGeneration || this.state.query.trim() !== query) return;
        const suggestions = employees.map(employee => ({
            id: `employee-suggestion-${employee.id}`,
            employeeSuggestion: true,
            employeeId: employee.id,
            label: employee.name,
            title: employee.name,
        }));
        this.items.unshift(...suggestions);
        this.state.focusedIndex = 0;
    },
    onSearchInput(ev) {
        if (this.env.searchModel.resModel === 'hr.employee' && !ev.target.value.trim()) {
            this.resetState();
            return;
        }
        return super.onSearchInput(ev);
    },
    selectItem(item) {
        if (!item.employeeSuggestion) return super.selectItem(item);
        this.resetState({ focus: false });
        return this.employeeAction.switchView('form', { resId: item.employeeId });
    },
});
