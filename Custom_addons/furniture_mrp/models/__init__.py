from . import uom_uom
from . import ir_ui_menu
from . import stock_picking
from . import stock_inventory_overview
from . import hr_employee
from . import mrp_stage_mixin
from . import mrp_worker_time_log
from . import hr_attendance
from . import mrp_production_order
from . import mrp_production_stage_warning
from . import mrp_stage_dashboard
from . import mrp_store_request
from . import mrp_production_line
from . import mrp_advance_material_release
from . import mrp_stage_product_batch
from . import mrp_material_reservation
from . import mrp_kit_planner
from . import mrp_stage_cost
from . import mrp_stage_cost_report
from . import mrp_priming
from . import mrp_painting
from . import mrp_carpentry
from . import mrp_bases
from . import mrp_sewing
from . import mrp_upholstery
from . import mrp_packaging
from . import mrp_finishing
from . import mrp_tailoring
from . import mrp_mps_line
from . import mrp_mps
from . import mrp_costing
from . import product_classification
from . import mrp_tailoring_material_setup
from . import mrp_mps_schedule
from . import hr_employee_compensation
from . import simple_payroll_slip
from . import payroll_slip_wizard
from . import payroll_batch_wizard
from . import payroll_accounting
from . import payroll_whatsapp
from . import sale_kit_allocation
from . import future_delivery_order
from . import mrp_stage_transfer_handoff
# Keep this import last: the lane flow extends the production order, its lines,
# stage models, stock valuation and products as one legacy-safe integration.
from . import mrp_lane_flow
# New production orders consume the preceding lane through explicit FIFO
# hand-offs.  Keep this after ``mrp_lane_flow`` because it extends its models.
from . import mrp_lane_handoff
from . import mrp_manual_quality
from . import bom_purchase_unit

from . import stage_visibility
