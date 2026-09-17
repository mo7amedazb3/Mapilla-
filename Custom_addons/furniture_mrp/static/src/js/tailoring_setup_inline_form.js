/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { isBinarySize } from "@web/core/utils/binary";
import { useBus, useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { imageUrl } from "@web/core/utils/urls";
import { FileUploader } from "@web/views/fields/file_handler";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { Component, onWillStart, status, useState, xml } from "@odoo/owl";

const TAILORING_SETUP_REFRESH_EVENT =
    "FURNITURE_MRP:TAILORING_SETUP_INLINE:REFRESH";
const tailoringMaterialPopupClosers = new Map();

function relationalId(value) {
    return Array.isArray(value) ? value[0] : value?.id || value;
}

export class FurniturePieceImagePreview extends Component {
    static props = { ...standardFieldProps };
    static template = xml`
        <button type="button"
                class="btn o_furniture_tailoring_setup_image_button is-readonly is-preview"
                t-att-title="previewTitle"
                t-att-aria-label="previewTitle"
                t-on-click.stop.prevent="openPreview">
            <img class="o_furniture_tailoring_direct_image_thumbnail"
                 t-att-src="thumbnailUrl"
                 t-att-alt="imageAlt"/>
            <span class="o_furniture_tailoring_setup_image_zoom"
                  aria-hidden="true">
                <i class="fa fa-search-plus"/>
            </span>
        </button>
    `;

    setup() {
        this.fileViewer = useFileViewer();
    }

    get isOrderImage() {
        return this.props.record.resModel ===
            "furniture.mrp.tailoring.setup.wizard";
    }

    get previewTitle() {
        return this.isOrderImage
            ? _t("عرض صورة الطقم بحجم كبير")
            : _t("عرض صورة القطعة بحجم كبير");
    }

    get imageAlt() {
        return this.isOrderImage
            ? _t("صورة الطقم داخل أمر الإنتاج")
            : _t("صورة القطعة داخل أمر الإنتاج");
    }

    get cacheToken() {
        return this.isOrderImage
            ? this.props.record.data.order_image_cache_token
            : this.props.record.data.image_cache_token;
    }

    get orderImageUrl() {
        const productionId = relationalId(
            this.props.record.data.production_id
        );
        return productionId
            ? imageUrl(
                "furniture.mrp.production",
                productionId,
                "tailoring_set_image_1920",
                { unique: this.cacheToken }
            )
            : false;
    }

    get thumbnailUrl() {
        const value = this.props.record.data[this.props.name];
        if (!value) {
            return false;
        }
        if (this.isOrderImage) {
            return this.orderImageUrl;
        }
        if (isBinarySize(value)) {
            return imageUrl(
                this.props.record.resModel,
                this.props.record.resId,
                this.props.name,
                {
                    unique: this.cacheToken,
                }
            );
        }
        const magic = { "/": "jpeg", R: "gif", i: "png", U: "webp" };
        return `data:image/${magic[value[0]] || "png"};base64,${value}`;
    }

    openPreview() {
        if (this.isOrderImage) {
            const source = this.orderImageUrl;
            if (!source) {
                return;
            }
            this.fileViewer.open({
                displayName: _t("صورة الطقم"),
                downloadUrl: source,
                defaultSource: source,
                isImage: true,
                isViewable: true,
                mimetype: "image/*",
            });
            return;
        }
        const productionLine = this.props.record.data.production_line_id;
        const productionLineId = Array.isArray(productionLine)
            ? productionLine[0]
            : productionLine?.id || productionLine;
        if (!productionLineId) {
            return;
        }
        const source = imageUrl(
            "furniture.mrp.production.line",
            productionLineId,
            "batch_image_1920",
            {
                unique: this.props.record.data.image_cache_token,
            }
        );
        this.fileViewer.open({
            displayName: _t("صورة القطعة"),
            downloadUrl: source,
            defaultSource: source,
            isImage: true,
            isViewable: true,
            mimetype: "image/*",
        });
    }
}

export class FurnitureOrderImageGallery extends Component {
    static components = { FileUploader };
    static props = {
        ...standardFieldProps,
        allowUpload: { type: Boolean, optional: true },
        acceptedFileExtensions: { type: String, optional: true },
    };
    static template = xml`
        <div class="o_furniture_tailoring_order_gallery"
             t-att-class="{ 'is-uploading': state.uploading }"
             aria-live="polite">
            <div t-foreach="state.images"
                 t-as="image"
                 t-key="image.key"
                 class="o_furniture_tailoring_order_gallery_item">
                <button type="button"
                        class="btn o_furniture_tailoring_order_gallery_preview"
                        t-att-title="image.display_name"
                        t-att-aria-label="image.display_name"
                        t-on-click.stop.prevent="() => this.openImage(image)">
                    <img t-att-src="image.url"
                         t-att-alt="image.display_name"/>
                    <span class="o_furniture_tailoring_setup_image_zoom"
                          aria-hidden="true">
                        <i class="fa fa-search-plus"/>
                    </span>
                </button>
                <button t-if="props.allowUpload"
                        type="button"
                        class="btn o_furniture_tailoring_order_gallery_remove"
                        title="إزالة الصورة"
                        aria-label="إزالة الصورة"
                        t-att-disabled="state.uploading or state.removingKey"
                        t-on-click.stop.prevent="() => this.removeImage(image)">
                    <i t-att-class="state.removingKey === image.key ? 'fa fa-spinner fa-spin' : 'fa fa-times'"
                       aria-hidden="true"/>
                </button>
            </div>
            <FileUploader t-if="props.allowUpload"
                acceptedFileExtensions="(props.acceptedFileExtensions ? props.acceptedFileExtensions + ',' : '') + 'dummy/allowAndroidCamera'"
                t-key="props.record.resId"
                onUploaded.bind="onUploaded"
                showUploadingText="false">
                <t t-set-slot="toggler">
                    <button type="button"
                            class="btn o_furniture_tailoring_order_gallery_add"
                            title="إضافة صورة أخرى"
                            aria-label="إضافة صورة أخرى"
                            t-att-disabled="state.uploading or state.removingKey">
                        <i t-if="state.uploading"
                           class="fa fa-spinner fa-spin"
                           aria-hidden="true"/>
                        <i t-else="" class="fa fa-plus" aria-hidden="true"/>
                    </button>
                </t>
            </FileUploader>
            <div t-if="!state.images.length and !props.allowUpload"
                 class="o_furniture_tailoring_setup_image_placeholder is-readonly"
                 title="لا توجد صور محفوظة للطقم">
                <i class="fa fa-camera" aria-hidden="true"/>
            </div>
        </div>
    `;

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.fileViewer = useFileViewer();
        this.state = useState({
            images: [],
            removingKey: false,
            uploading: false,
        });
        onWillStart(() => this.loadImages());
    }

    decorateImages(images) {
        return (images || []).map((item) => ({
            ...item,
            url: imageUrl(item.res_model, item.res_id, item.res_field, {
                unique: item.token || item.res_id,
            }),
        }));
    }

    async loadImages() {
        if (!this.props.record.resId) {
            this.state.images = [];
            return;
        }
        const images = await this.orm.call(
            "furniture.mrp.tailoring.setup.wizard",
            "get_order_image_gallery",
            [[this.props.record.resId]]
        );
        if (status(this) === "mounted" || status(this) === "new") {
            this.state.images = this.decorateImages(images);
        }
    }

    openImage(image) {
        this.fileViewer.open({
            displayName: image.display_name || _t("صورة الطقم"),
            downloadUrl: image.url,
            defaultSource: image.url,
            isImage: true,
            isViewable: true,
            mimetype: "image/*",
        });
    }

    async onUploaded(info) {
        if (this.state.uploading || this.state.removingKey) {
            return;
        }
        if (info.type && !info.type.startsWith("image/")) {
            this.notification.add(_t("اختار ملف صورة."), { type: "danger" });
            return;
        }
        this.state.uploading = true;
        try {
            const result = await this.orm.call(
                "furniture.mrp.tailoring.setup.wizard",
                "add_order_image",
                [[this.props.record.resId], info.data, info.name || false]
            );
            if (status(this) !== "mounted") {
                return;
            }
            this.state.images = this.decorateImages(result?.images || []);
            this.notification.add(_t("تمت إضافة الصورة."), {
                type: "success",
            });
        } catch (error) {
            const message =
                error?.data?.arguments?.[0] ||
                error?.data?.message ||
                error?.message ||
                _t("تعذر إضافة الصورة.");
            this.notification.add(message, { type: "danger" });
        } finally {
            if (status(this) === "mounted") {
                this.state.uploading = false;
            }
        }
    }

    async removeImage(image) {
        if (this.state.uploading || this.state.removingKey) {
            return;
        }
        this.state.removingKey = image.key;
        try {
            const result = await this.orm.call(
                "furniture.mrp.tailoring.setup.wizard",
                "remove_order_image",
                [[this.props.record.resId], image.key]
            );
            if (status(this) !== "mounted") {
                return;
            }
            this.state.images = this.decorateImages(result?.images || []);
            this.notification.add(_t("تم حذف الصورة."), {
                type: "success",
            });
        } catch (error) {
            const message =
                error?.data?.arguments?.[0] ||
                error?.data?.message ||
                error?.message ||
                _t("تعذر حذف الصورة.");
            this.notification.add(message, { type: "danger" });
        } finally {
            if (status(this) === "mounted") {
                this.state.removingKey = false;
            }
        }
    }
}

export const furniturePieceImagePreviewField = {
    component: FurniturePieceImagePreview,
    displayName: _t("Piece image preview"),
    supportedTypes: ["binary"],
    fieldDependencies: [
        {
            name: "production_line_id",
            type: "many2one",
            relation: "furniture.mrp.production.line",
        },
        { name: "image_cache_token", type: "char" },
    ],
    isEmpty: () => false,
};

export const furnitureOrderImagePreviewField = {
    component: FurnitureOrderImageGallery,
    displayName: _t("Order image gallery preview"),
    supportedTypes: ["binary"],
    fieldDependencies: [
        {
            name: "production_id",
            type: "many2one",
            relation: "furniture.mrp.production",
        },
        { name: "order_image_cache_token", type: "char" },
    ],
    isEmpty: () => false,
    extractProps: () => ({ allowUpload: false }),
};

export class FurniturePieceImageUpload extends Component {
    static components = { FileUploader };
    static props = {
        ...standardFieldProps,
        acceptedFileExtensions: { type: String, optional: true },
    };
    static template = xml`
        <div class="o_furniture_tailoring_direct_image_upload"
             t-att-class="{ 'is-uploading': state.uploading }"
             aria-live="polite">
            <FileUploader
                acceptedFileExtensions="(props.acceptedFileExtensions ? props.acceptedFileExtensions + ',' : '') + 'dummy/allowAndroidCamera'"
                t-key="props.record.resId"
                onUploaded.bind="onUploaded"
                showUploadingText="false">
                <t t-set-slot="toggler">
                    <button type="button"
                            class="btn o_furniture_tailoring_setup_image_button"
                            t-att-title="uploadTitle"
                            t-att-disabled="state.uploading">
                        <img t-if="hasImage"
                             class="o_furniture_tailoring_direct_image_thumbnail"
                             t-att-src="imageDataUrl"
                             t-att-alt="imageAlt"/>
                        <i t-else="" class="fa fa-camera" aria-hidden="true"/>
                        <span t-if="state.uploading"
                              class="o_furniture_tailoring_direct_image_spinner">
                            <i class="fa fa-spinner fa-spin" aria-hidden="true"/>
                        </span>
                        <span t-elif="hasImage"
                              class="o_furniture_tailoring_setup_image_zoom">
                            <i class="fa fa-pencil" aria-hidden="true"/>
                        </span>
                        <span class="visually-hidden">
                            <t t-esc="uploadTitle"/>
                        </span>
                    </button>
                </t>
            </FileUploader>
        </div>
    `;

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            cacheToken: false,
            preview: false,
            previewMimetype: false,
            uploading: false,
        });
    }

    get isOrderImage() {
        return this.props.record.resModel ===
            "furniture.mrp.tailoring.setup.wizard";
    }

    get imageAlt() {
        return this.isOrderImage
            ? _t("صورة الطقم داخل أمر الإنتاج")
            : _t("صورة القطعة داخل أمر الإنتاج");
    }

    get uploadTitle() {
        if (this.isOrderImage) {
            return this.hasImage
                ? _t("تغيير صورة الطقم")
                : _t("اختيار صورة للطقم");
        }
        return this.hasImage
            ? _t("تغيير صورة القطعة")
            : _t("اختيار صورة للقطعة");
    }

    get recordCacheToken() {
        return this.isOrderImage
            ? this.props.record.data.order_image_cache_token
            : this.props.record.data.image_cache_token;
    }

    get orderImageUrl() {
        const productionId = relationalId(
            this.props.record.data.production_id
        );
        return productionId
            ? imageUrl(
                "furniture.mrp.production",
                productionId,
                "tailoring_set_image_1920",
                {
                    unique:
                        this.state.cacheToken ||
                        this.recordCacheToken,
                }
            )
            : false;
    }

    get hasImage() {
        return Boolean(
            this.state.preview || this.props.record.data[this.props.name]
        );
    }

    get imageDataUrl() {
        if (!this.hasImage) {
            return false;
        }
        if (this.state.preview) {
            const magic = { "/": "jpeg", R: "gif", i: "png", U: "webp" };
            const mimetype =
                this.state.previewMimetype ||
                `image/${magic[this.state.preview[0]] || "png"}`;
            return `data:${mimetype};base64,${this.state.preview}`;
        }
        if (this.isOrderImage) {
            return this.orderImageUrl;
        }
        const value = this.props.record.data[this.props.name];
        if (isBinarySize(value)) {
            return imageUrl(
                this.props.record.resModel,
                this.props.record.resId,
                this.props.name,
                {
                    unique:
                        this.state.cacheToken ||
                        this.recordCacheToken,
                }
            );
        }
        const magic = { "/": "jpeg", R: "gif", i: "png", U: "webp" };
        return `data:image/${magic[value[0]] || "png"};base64,${value}`;
    }

    async onUploaded(info) {
        if (this.state.uploading) {
            return;
        }
        if (info.type && !info.type.startsWith("image/")) {
            this.notification.add(_t("اختار ملف صورة."), { type: "danger" });
            return;
        }
        this.state.uploading = true;
        try {
            const result = await this.orm.call(
                this.isOrderImage
                    ? "furniture.mrp.tailoring.setup.wizard"
                    : "furniture.mrp.tailoring.setup.wizard.line",
                this.isOrderImage
                    ? "upload_order_image"
                    : "upload_piece_image",
                [[this.props.record.resId], info.data]
            );
            if (status(this) !== "mounted") {
                return;
            }
            this.state.cacheToken = result?.image_token || false;
            this.state.preview = info.data;
            this.state.previewMimetype = info.type || false;
            this.notification.add(
                this.isOrderImage
                    ? _t("تم حفظ صورة الطقم.")
                    : _t("تم حفظ صورة القطعة."), {
                type: "success",
            });
        } catch (error) {
            const message =
                error?.data?.arguments?.[0] ||
                error?.data?.message ||
                error?.message ||
                (this.isOrderImage
                    ? _t("تعذر حفظ صورة الطقم.")
                    : _t("تعذر حفظ صورة القطعة."));
            this.notification.add(message, { type: "danger" });
        } finally {
            if (status(this) === "mounted") {
                this.state.uploading = false;
            }
        }
    }
}

export const furniturePieceImageUploadField = {
    component: FurniturePieceImageUpload,
    displayName: _t("Direct piece image upload"),
    supportedTypes: ["binary"],
    fieldDependencies: [{ name: "image_cache_token", type: "char" }],
    isEmpty: () => false,
    extractProps: ({ options }) => ({
        acceptedFileExtensions:
            options.accepted_file_extensions || "image/jpeg,image/png",
    }),
};

export const furnitureOrderImageUploadField = {
    component: FurnitureOrderImageGallery,
    displayName: _t("Order image gallery upload"),
    supportedTypes: ["binary"],
    fieldDependencies: [
        {
            name: "production_id",
            type: "many2one",
            relation: "furniture.mrp.production",
        },
        { name: "order_image_cache_token", type: "char" },
    ],
    isEmpty: () => false,
    extractProps: ({ options }) => ({
        allowUpload: true,
        acceptedFileExtensions:
            options.accepted_file_extensions || "image/jpeg,image/png",
    }),
};

export class FurniturePieceNoteInput extends Component {
    static props = { ...standardFieldProps };
    static template = xml`
        <div class="o_furniture_tailoring_note_input_wrap"
             t-att-class="{ 'is-saving': state.saving }">
            <i class="fa fa-sticky-note-o" aria-hidden="true"/>
            <textarea class="o_furniture_tailoring_note_input"
                      rows="2"
                      maxlength="2000"
                      placeholder="اكتب ملاحظات الصنف..."
                      t-model="state.value"
                      t-att-disabled="state.saving"
                      t-on-click.stop="stopEvent"
                      t-on-keydown.stop="stopEvent"
                      t-on-blur="save"/>
            <i t-if="state.saving"
               class="fa fa-spinner fa-spin o_furniture_tailoring_note_spinner"
               aria-hidden="true"/>
        </div>
    `;

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        const initialValue = this.props.record.data[this.props.name] || "";
        this.state = useState({
            savedValue: initialValue,
            saving: false,
            value: initialValue,
        });
    }

    stopEvent() {}

    async save() {
        const value = this.state.value.trim();
        if (this.state.saving || value === this.state.savedValue) {
            return;
        }
        this.state.saving = true;
        try {
            const result = await this.orm.call(
                "furniture.mrp.tailoring.setup.wizard.line",
                "save_piece_note",
                [[this.props.record.resId], value]
            );
            if (status(this) !== "mounted") {
                return;
            }
            this.state.value = result?.note || "";
            this.state.savedValue = this.state.value;
        } catch (error) {
            if (status(this) !== "mounted") {
                return;
            }
            this.state.value = this.state.savedValue;
            const message =
                error?.data?.arguments?.[0] ||
                error?.data?.message ||
                error?.message ||
                _t("تعذر حفظ ملاحظة الصنف.");
            this.notification.add(message, { type: "danger" });
        } finally {
            if (status(this) === "mounted") {
                this.state.saving = false;
            }
        }
    }
}

export const furniturePieceNoteInputField = {
    component: FurniturePieceNoteInput,
    displayName: _t("Direct piece note input"),
    supportedTypes: ["text"],
    isEmpty: () => false,
};

export class FurnitureTailoringSetupInlineFormController extends FormController {
    setup() {
        super.setup();
        useBus(this.env.bus, TAILORING_SETUP_REFRESH_EVENT, async (event) => {
            const request = event.detail || {};
            const currentResId = this.model.root.resId;

            if (request.claimed) {
                return;
            }
            if (request.resModel && request.resModel !== this.props.resModel) {
                return;
            }
            if (
                request.resId &&
                (!currentResId || String(request.resId) !== String(currentResId))
            ) {
                return;
            }

            // Claim synchronously before the first await so only the intended
            // setup form resolves this request when more than one form exists.
            request.claimed = true;
            try {
                await this.model.load();
                request.resolve(true);
            } catch (error) {
                request.reject(error);
            }
        });
    }
}

export const furnitureTailoringSetupInlineFormView = {
    ...formView,
    Controller: FurnitureTailoringSetupInlineFormController,
};

/**
 * Reload the already-open setup form without opening/replacing any action.
 * If the form is no longer mounted, resolve as a harmless no-op rather than
 * leaving the originating Odoo button permanently disabled.
 */
function refreshTailoringSetupInlineForm(env, action) {
    const params = action.params || {};
    const resId = params.res_id || params.wizard_id || false;
    const resModel = params.res_model || "furniture.mrp.tailoring.setup.wizard";

    return new Promise((resolve, reject) => {
        const request = {
            claimed: false,
            reject,
            resId,
            resModel,
            resolve,
        };
        env.bus.trigger(TAILORING_SETUP_REFRESH_EVENT, request);
        if (!request.claimed) {
            resolve(false);
        }
    });
}

/**
 * Open the material editor as a true dialog layered over the setup dialog.
 * Using the dialog service directly is intentional: an act_window with
 * target="new" replaces Odoo's currently tracked action dialog.
 */
function openTailoringMaterialPopup(env, action) {
    const params = action.params || {};
    if (!params.res_model || !params.res_id) {
        return;
    }
    const popupKey = String(params.res_id);
    let closeDialog;
    closeDialog = env.services.dialog.add(
        FormViewDialog,
        {
            resModel: params.res_model,
            resId: params.res_id,
            viewId: params.view_id || false,
            context: params.context || {},
            mode: "edit",
            title: params.title || _t("إضافة الخامات"),
            size: params.size || "xl",
        },
        {
            onClose: () => {
                if (tailoringMaterialPopupClosers.get(popupKey) === closeDialog) {
                    tailoringMaterialPopupClosers.delete(popupKey);
                }
            },
        }
    );
    tailoringMaterialPopupClosers.set(popupKey, closeDialog);
}

/**
 * Saving refreshes the setup form still mounted below this dialog.  The
 * registered closer then removes this exact child dialog.  Keeping closure
 * after the successful server action leaves validation errors visible.
 */
async function finishTailoringMaterialPopup(env, action) {
    await refreshTailoringSetupInlineForm(env, action);
    const editorId = action.params?.editor_id;
    const popupKey = editorId ? String(editorId) : false;
    const closeDialog = popupKey
        ? tailoringMaterialPopupClosers.get(popupKey)
        : false;
    if (closeDialog) {
        closeDialog();
    }
}

registry
    .category("views")
    .add("furniture_tailoring_setup_inline_form", furnitureTailoringSetupInlineFormView);
registry
    .category("fields")
    .add("furniture_piece_image_preview", furniturePieceImagePreviewField);
registry
    .category("fields")
    .add("furniture_piece_image_upload", furniturePieceImageUploadField);
registry
    .category("fields")
    .add("furniture_order_image_preview", furnitureOrderImagePreviewField);
registry
    .category("fields")
    .add("furniture_order_image_upload", furnitureOrderImageUploadField);
registry
    .category("fields")
    .add("furniture_piece_note_input", furniturePieceNoteInputField);
registry
    .category("actions")
    .add("furniture_tailoring_setup_refresh", refreshTailoringSetupInlineForm);
registry
    .category("actions")
    .add("furniture_tailoring_material_popup_open", openTailoringMaterialPopup);
registry
    .category("actions")
    .add("furniture_tailoring_material_popup_saved", finishTailoringMaterialPopup);
