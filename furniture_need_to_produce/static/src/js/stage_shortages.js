/** @odoo-module **/

// One destination per action: domains, counts, paging and bulk approval all
// share the same immutable scope. These are existing Min/Max policies, not
// newly-created buffers or alternative stock calculations.
export const STAGE_SHORTAGE_OPTIONS = Object.freeze([
    {code: "carpentry", lane: "frame", label: "النجارة", icon: "fa-gavel"},
    {code: "bases", lane: "bases", label: "القواعد", icon: "fa-cubes"},
    {code: "finishing", lane: "preparation", label: "التجهيز", icon: "fa-magic"},
    {code: "tailoring", lane: "tailoring", label: "التفصيل", icon: "fa-scissors"},
    {code: "painting", lane: "painting", label: "الدهانات", icon: "fa-paint-brush"},
]);

export function stageShortageAction(code) {
    const stage = STAGE_SHORTAGE_OPTIONS.find(option => option.code === code);
    return stage ? `furniture_need_to_produce.action_stage_shortages_${stage.lane}` : null;
}
