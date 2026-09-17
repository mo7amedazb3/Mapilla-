/** @odoo-module **/

import { onWillStart, useEffect, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import {
    Many2OneField,
    many2OneField,
} from "@web/views/fields/many2one/many2one_field";

export class FurnitureModelNavigator extends Many2OneField {
    static template = "furniture_mrp.FurnitureModelNavigator";

    setup() {
        super.setup();
        this.openingRecipe = false;
        this.cardState = useState({
            models: [],
            loading: true,
            loadFailed: false,
            switchingModelId: false,
        });
        onWillStart(async () => {
            await this.loadModels();
        });
        useEffect(
            () => {
                this.openInitialRecipe();
            },
            () => [
                this.props.record.resId,
                this.props.record.data.furniture_recipe_model_id?.[0],
                this.props.name,
            ]
        );
    }

    async loadModels() {
        try {
            const models = await this.orm.searchRead(
                this.relation,
                this.getDomain(),
                ["name", "sequence"],
                {
                    context: this.context,
                    order: "sequence, name, id",
                }
            );
            const selectedModelId = this.selectedModelId;
            if (
                selectedModelId &&
                !models.some((model) => model.id === selectedModelId)
            ) {
                const [selectedModel] = await this.orm.read(
                    this.relation,
                    [selectedModelId],
                    ["name", "sequence"],
                    { context: this.context }
                );
                if (selectedModel) {
                    models.unshift(selectedModel);
                }
            }
            this.cardState.models = models;
        } catch (error) {
            this.cardState.loadFailed = true;
            this.notification.add(
                _t("تعذر تحميل قائمة الموديلات. حدّث الصفحة وحاول مرة أخرى."),
                {
                    title: _t("تعذر عرض الموديلات"),
                    type: "warning",
                }
            );
        } finally {
            this.cardState.loading = false;
        }
    }

    get selectedModelId() {
        const value = this.props.record.data[this.props.name];
        return value && value[0];
    }

    isSelected(modelId) {
        return modelId === this.selectedModelId;
    }

    async selectModel(model) {
        if (this.cardState.switchingModelId) {
            return;
        }

        const record = this.props.record;
        if (
            record.resModel !== "mrp.bom" ||
            !record.resId ||
            !this.isRecipeNavigationField()
        ) {
            return super.updateRecord([model.id, model.name]);
        }

        if (
            record.data.furniture_is_model_recipe &&
            model.id === this.selectedModelId
        ) {
            return;
        }

        if (await record.isDirty()) {
            this.notification.add(
                _t("احفظ تعديلات خامات الموديل الحالي قبل فتح موديل آخر."),
                {
                    title: _t("توجد تعديلات غير محفوظة"),
                    type: "warning",
                }
            );
            return;
        }

        this.cardState.switchingModelId = model.id;
        try {
            await this.openRecipe(model.id);
        } finally {
            this.cardState.switchingModelId = false;
        }
    }

    isRecipeNavigationField() {
        const { data } = this.props.record;
        if (data.type !== "normal") {
            return false;
        }
        return (
            (!data.furniture_is_model_recipe &&
                this.props.name === "furniture_recipe_model_id") ||
            (data.furniture_is_model_recipe &&
                this.props.name === "furniture_model_id")
        );
    }

    async openRecipe(modelId) {
        if (this.openingRecipe) {
            return;
        }
        this.openingRecipe = true;
        try {
            const record = this.props.record;
            const recipeId = await this.orm.call(
                "mrp.bom",
                "action_select_furniture_recipe_model",
                [[record.resId], modelId],
                { context: record.context }
            );
            await record.model.load({
                resId: recipeId,
                resIds: [recipeId],
            });
        } finally {
            this.openingRecipe = false;
        }
    }

    async openInitialRecipe() {
        const record = this.props.record;
        if (
            !record.resId ||
            record.resModel !== "mrp.bom" ||
            this.props.name !== "furniture_recipe_model_id" ||
            record.data.type !== "normal" ||
            record.data.furniture_is_model_recipe ||
            (await record.isDirty())
        ) {
            return;
        }
        const currentValue = record.data.furniture_recipe_model_id;
        const currentModelId = currentValue && currentValue[0];
        if (currentModelId) {
            await this.openRecipe(currentModelId);
        }
    }

    async updateRecord(value) {
        const record = this.props.record;
        if (
            record.resModel !== "mrp.bom" ||
            !record.resId ||
            !this.isRecipeNavigationField()
        ) {
            return super.updateRecord(value);
        }

        const selectedModelId = value && value[0];
        const currentValue = record.data[this.props.name];
        const currentModelId = currentValue && currentValue[0];
        if (
            !selectedModelId ||
            (record.data.furniture_is_model_recipe &&
                selectedModelId === currentModelId)
        ) {
            record.model.notify();
            return;
        }

        if (await record.isDirty()) {
            this.notification.add(
                _t("احفظ تعديلات خامات الموديل الحالي قبل فتح موديل آخر."),
                {
                    title: _t("توجد تعديلات غير محفوظة"),
                    type: "warning",
                }
            );
            record.model.notify();
            return;
        }

        await this.openRecipe(selectedModelId);
    }
}

registry.category("fields").add("furniture_model_navigator", {
    ...many2OneField,
    component: FurnitureModelNavigator,
});
