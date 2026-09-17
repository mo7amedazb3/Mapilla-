from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestAttendanceLayout(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Employee = cls.env["hr.employee"]
        cls.department = cls.env["hr.department"].create({"name": "Layout isolated workers"})
        cls.officer = new_test_user(cls.env, login="attendance_layout_officer",
                                   groups="hr_attendance.group_hr_attendance_officer")

    def employee(self, name, codes=(), basis="time", role="worker", department=None):
        values = {"name": name, "department_id": (department or self.department).id}
        if "furniture_pay_basis" in self.Employee._fields:
            values.update(furniture_pay_basis=basis, furniture_mrp_role=role)
            stages = self.env["furniture.mrp.employee.stage"].search([("code", "in", list(codes))])
            self.assertEqual(len(stages), len(codes))
            stage_field = "furniture_mrp_supervisor_stage_ids" if role == "supervisor" else "furniture_mrp_worker_stage_ids"
            values[stage_field] = [(6, 0, stages.ids)]
        return self.Employee.create(values)

    def test_stage_order_piece_workers_last_no_duplicates(self):
        if "furniture_pay_basis" not in self.Employee._fields:
            self.skipTest("Optional MRP classifications")
        assembly = self.employee("A assembly", ["carpentry"])
        upholstery = self.employee("B upholstery", ["upholstery"])
        production = self.employee("A piece priming", ["priming"], "production")
        priming = self.employee("Z priming", ["priming"])
        bases = self.employee("D bases", ["bases"])
        finishing = self.employee("C finishing", ["finishing"])
        supervisor = self.employee("Y multi-stage", ["priming", "carpentry"], role="supervisor")
        employees = assembly | upholstery | production | priming | bases | finishing | supervisor
        names = ["department_id", "furniture_pay_basis", "furniture_mrp_worker_stage_ids",
                 "furniture_mrp_supervisor_stage_ids", "write_date"]
        before = employees.read(names)
        data = self.Employee.with_user(self.officer).factory_attendance_dashboard_data(department_id=self.department.id)
        self.assertEqual([r["id"] for r in data["employees"]],
                         [supervisor.id, priming.id, assembly.id, bases.id, finishing.id, upholstery.id, production.id])
        self.assertEqual([g["key"] for g in data["attendance_groups"]],
                         ["time:stage:priming", "time:stage:carpentry", "time:stage:bases",
                          "time:stage:finishing", "time:stage:upholstery", "production:stage:priming"])
        flattened = [r["id"] for group in data["attendance_groups"] for r in group["employees"]]
        self.assertEqual(flattened, [r["id"] for r in data["employees"]])
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(employees.read(names), before)

    def test_hr_department_fallback_precedes_stale_factory_category(self):
        department = self.env["hr.department"].create({"name": "تفصيل"})
        worker = self.employee("Fallback tailoring", department=department)
        if "furniture_mrp_factory_department" in worker._fields:
            worker.furniture_mrp_factory_department = "upholstery"
        self.assertEqual(worker._factory_attendance_section()[1], "stage:tailoring")

    def test_unknown_departments_grouped_without_losing_workers(self):
        self.employee("Unknown team Z")
        self.employee("Unknown team A")
        data = self.Employee.with_user(self.officer).factory_attendance_dashboard_data(department_id=self.department.id)
        self.assertEqual(len(data["attendance_groups"]), 1)
        self.assertEqual([row["name"] for row in data["employees"]], ["Unknown team A", "Unknown team Z"])
        self.assertEqual(data["counts"]["all"], 2)

    def test_status_filters_keep_groups_consistent_and_empty(self):
        self.employee("Absent worker")
        data = self.Employee.with_user(self.officer).factory_attendance_dashboard_data(
            department_id=self.department.id, status="lunch")
        self.assertEqual(data["employees"], [])
        self.assertEqual(data["attendance_groups"], [])
        self.assertEqual(data["counts"]["all"], 1)

    def test_unassigned_carpenter_not_falsely_assigned_a_stage(self):
        department = self.env["hr.department"].create({"name": "نجارة"})
        worker = self.employee("Unassigned carpenter", department=department)
        self.assertEqual(worker._factory_attendance_section()[1], "department:%s" % department.id)
