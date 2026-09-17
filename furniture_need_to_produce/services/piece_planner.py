"""Deterministic, database-free planning of one furniture item at a time.

``pools`` contains available (already unreserved) quantities by output role. Each
entry is ``{kind: 'stock'|'incoming', source_id: int, quantity: float}``. List order
is FIFO within each kind; physically ready stock is always consumed first. The
caller must supply a single product/model/company's pools, share them between
successive pieces, and lock/revalidate the sources when committing an approval.
This module allocates a preview, not a physical stock reservation.

Successful calls mutate only the quantities in the supplied pools. Validation or
planning errors leave them untouched. Use ``deepcopy(pools)`` for a disposable
preview. A unique ``key_prefix`` makes stage references unique across pieces.

Temporary timing is two working hours per unit per actual operation. It is not
an MPS schedule: parallel branches use their longest dependency path, a working
day is ten hours, and an incoming order's unknown waiting time is never guessed.
"""

from copy import deepcopy
from math import isfinite


HOURS_PER_OPERATION = 2.0
HOURS_PER_DAY = 10.0
EPSILON = 1e-9

LANE_STAGES = {
    "frame": ("priming", "carpentry"),
    "bases": ("bases",),
    "preparation": ("finishing",),
    "tailoring": ("tailoring",),
    "painting": ("painting",),
    "upholstery": ("upholstery",),
    "packaging": ("packaging",),
}
OUTPUT_ROLES = {
    "frame": "frame",
    "bases": "bases",
    "preparation": "finish",
    "tailoring": "tailoring",
    "painting": "painting",
    "upholstery": "upholstery",
    "packaging": "packaging",
}
REQUIRED_INPUTS = {
    "frame": (),
    "bases": ("frame",),
    "preparation": ("bases",),
    "tailoring": (),
    "painting": (),
    "upholstery": ("finish", "tailoring"),
    "packaging": ("upholstery", "painting"),
}
STAGE_LABELS = {
    "priming": "التقديم",
    "carpentry": "التجميع",
    "bases": "القواعد",
    "finishing": "التجهيز",
    "tailoring": "التفصيل",
    "painting": "الدهانات",
    "upholstery": "الكسوة",
    "packaging": "التغليف",
}
_ROLE_LANES = {role: lane for lane, role in OUTPUT_ROLES.items()}


def _number(value, description, positive=False):
    if isinstance(value, bool):
        raise ValueError("%s must be a finite number" % description)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("%s must be a finite number" % description) from exc
    if not isfinite(number) or number < 0 or (positive and number <= EPSILON):
        raise ValueError("%s must be %s" % (
            description, "positive and finite" if positive else "nonnegative and finite",
        ))
    return number


def _validated_pool_copy(pools):
    if not isinstance(pools, dict):
        raise ValueError("pools must be a dictionary keyed by output role")
    working = deepcopy(pools)
    for role, entries in working.items():
        if role not in _ROLE_LANES:
            raise ValueError("Unknown output role: %s" % role)
        if not isinstance(entries, list):
            raise ValueError("Pool entries must be lists")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Each pool entry must be a dictionary")
            if entry.get("kind") not in ("stock", "incoming"):
                raise ValueError("Pool kind must be stock or incoming")
            source_id = entry.get("source_id")
            if isinstance(source_id, bool) or not isinstance(source_id, int):
                raise ValueError("Pool source_id must be an integer")
            source = (entry["kind"], source_id)
            if source in seen:
                raise ValueError("Duplicate pool source in role %s: %s" % (role, source))
            seen.add(source)
            entry["quantity"] = _number(entry.get("quantity"), "Source quantity")
    return working


def format_route(plan, labels=None):
    """Return a parenthesized route of production operations, omitting stock.

    A ``+`` joins parallel producer branches, not sequential operations. Incoming
    orders are explicitly labelled as waiting; their historical operations are
    not scheduled again. Supply ``labels`` to translate operation names.
    """
    labels = STAGE_LABELS if labels is None else labels
    stages = {stage["key"]: stage for stage in plan["stages"]}

    def render_sources(sources):
        branches = []
        for source in sources:
            if source["kind"] == "stock":
                continue
            if source["kind"] == "incoming":
                text = "انتظار أمر جارٍ (%s)" % source["source_id"]
            else:
                stage = stages[source["source_key"]]
                upstream = render_sources(stage["inputs"])
                own = (" + " if stage.get('parallel_operations') else " → ").join(
                    labels.get(code, code) for code in stage["stage_codes"])
                if stage.get('parallel_operations'):
                    own = '(%s)' % own
                text = "%s → %s" % (upstream, own) if upstream else own
            if text not in branches:
                branches.append(text)
        if len(branches) > 1:
            return "(%s)" % " + ".join(branches)
        return branches[0] if branches else ""

    return render_sources(plan["outputs"]) or "مغطى من المخزون الجاهز"


def plan_piece(target_lane="packaging", pools=None, quantity=1.0, key_prefix="", parallel_finish=False):
    """Allocate sources and return only the missing operations for this piece.

    ``stages`` is topologically ordered; each stage's ``inputs`` references stock,
    an incoming order, or an earlier stage through ``source_key``. A source can
    cover a fractional quantity, so mixed available/new production never rounds
    a shortage up to another full piece. One frame lane contains two sequential
    operations. Bases/preparation are separate in the historical cycle; opt-in
    parallel_finish combines them into one stage record with two operations.

    ``critical_path_hours``/``critical_path_days`` count known working duration,
    excluding unknown incoming waits. If any such wait exists,
    ``critical_path_is_lower_bound`` is true and ``estimated_completion_hours``
    is None. None of these values includes queues, shifts or calendar holidays.
    """
    if target_lane not in LANE_STAGES:
        raise ValueError("Unknown target lane: %s" % target_lane)
    quantity = _number(quantity, "Requested quantity", positive=True)
    if not isinstance(key_prefix, str):
        raise ValueError("key_prefix must be a string")
    pools = {} if pools is None else pools
    working = _validated_pool_copy(pools)
    # Historical callers keep the serial route. New approvals have one shared
    # frame input, two concurrent operations, and only one joined finish output.
    lane_stages = dict(LANE_STAGES)
    required_inputs = dict(REQUIRED_INPUTS)
    role_lanes = dict(_ROLE_LANES)
    target_role = OUTPUT_ROLES[target_lane]
    if parallel_finish:
        lane_stages['finish'] = ('bases', 'finishing')
        required_inputs['finish'] = ('frame',)
        role_lanes['finish'] = 'finish'
        if target_lane in ('bases', 'preparation'):
            target_role = 'finish'
    stages = []
    stage_by_key = {}
    incoming = []

    def allocate(role, needed):
        sources = []
        remaining = needed
        for kind in ("stock", "incoming"):
            for entry in working.get(role, []):
                if entry["kind"] != kind or entry["quantity"] <= EPSILON:
                    continue
                if remaining <= EPSILON:
                    break
                allocated = min(remaining, entry["quantity"])
                source = {
                    "role": role,
                    "kind": kind,
                    "source_id": entry["source_id"],
                    "quantity": allocated,
                }
                sources.append(source)
                entry["quantity"] = max(0.0, entry["quantity"] - allocated)
                remaining = max(0.0, remaining - allocated)
                if kind == "incoming":
                    incoming.append(dict(source, wait_hours=None))
        if remaining > EPSILON:
            lane = role_lanes[role]
            inputs = []
            for required_role in required_inputs[lane]:
                inputs.extend(allocate(required_role, remaining))
            codes = lane_stages[lane]
            key = "%s%s-%02d" % (key_prefix, lane, len(stages) + 1)
            dependencies = [source["source_key"] for source in inputs if source["kind"] == "stage"]
            upstream_hours = max(
                (stage_by_key[dependency]["critical_path_hours"] for dependency in dependencies),
                default=0.0,
            )
            own_hours = HOURS_PER_OPERATION * len(codes) * remaining
            elapsed_hours = HOURS_PER_OPERATION * remaining if lane == 'finish' else own_hours
            unknown_wait = any(source["kind"] == "incoming" for source in inputs) or any(
                stage_by_key[dependency]["has_unknown_incoming_wait"] for dependency in dependencies
            )
            stage = {
                "key": key,
                "lane": lane,
                "stage_code": codes[0],
                "stage_codes": list(codes),
                "output_role": role,
                "quantity": remaining,
                "estimated_hours": own_hours,
                "estimated_days": own_hours / HOURS_PER_DAY,
                "operations": [{
                    "stage_code": code,
                    "estimated_hours": HOURS_PER_OPERATION * remaining,
                } for code in codes],
                "inputs": inputs,
                "dependencies": dependencies,
                "critical_path_hours": upstream_hours + elapsed_hours,
                "has_unknown_incoming_wait": unknown_wait,
            }
            if lane == 'finish':
                stage['parallel_operations'] = True
            stages.append(stage)
            stage_by_key[key] = stage
            sources.append({
                "role": role,
                "kind": "stage",
                "source_id": None,
                "source_key": key,
                "quantity": remaining,
            })
        return sources

    outputs = allocate(target_role, quantity)
    critical_path = max((
        stage_by_key[source["source_key"]]["critical_path_hours"]
        for source in outputs if source["kind"] == "stage"
    ), default=0.0)
    total_hours = sum(stage["estimated_hours"] for stage in stages)
    plan = {
        "target_lane": target_lane,
        "quantity": quantity,
        "stages": stages,
        "outputs": outputs,
        "total_work_hours": total_hours,
        "total_work_days": total_hours / HOURS_PER_DAY,
        "critical_path_hours": critical_path,
        "critical_path_days": critical_path / HOURS_PER_DAY,
        "has_unknown_incoming_wait": bool(incoming),
        "critical_path_is_lower_bound": bool(incoming),
        "estimated_completion_hours": None if incoming else critical_path,
        "incoming_waits": incoming,
        "hours_per_operation": HOURS_PER_OPERATION,
        "hours_per_day": HOURS_PER_DAY,
    }
    plan["route_text"] = format_route(plan)
    if parallel_finish:
        plan['cycle_version'] = 'parallel_finish_v1'
    # Publish consumption only after the entire plan succeeds.
    for role, entries in working.items():
        for original, used in zip(pools[role], entries):
            original["quantity"] = used["quantity"]
    return plan
