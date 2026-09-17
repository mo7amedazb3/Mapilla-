"""Read-only day summary. All input timestamps are naive UTC, as in Odoo.

Never merge or manufacture stored punches. A confirmed lunch gap credits at
most one paid hour per day; any confirmed excess remains deductible time.
"""
from datetime import datetime, time, timedelta

import pytz


PAID_LUNCH_SECONDS = 60 * 60


def summarize_day(intervals, selected_date, zone, now, lunch_enabled=True):
    def utc_at(day, hour):
        return zone.localize(datetime.combine(day, time(hour))).astimezone(
            pytz.UTC
        ).replace(tzinfo=None)

    day_start = utc_at(selected_date, 0)
    day_end = utc_at(selected_date + timedelta(days=1), 0)
    lunch_start, deadline = utc_at(selected_date, 13), utc_at(selected_date, 16)
    intervals = sorted((
        (start, end or None)
        for start, end in intervals
        if start < day_end and start <= now and (not end or end > day_start)
    ), key=lambda interval: interval[0])
    # Union in memory only: legacy overlapping rows must not turn occupied time
    # into a paid gap or credit the same minute twice.
    merged = []
    for start, end in intervals:
        if merged and (merged[-1][1] is None or start <= merged[-1][1]):
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end)
                          if previous_end and end else None)
        else:
            merged.append((start, end))
    intervals = merged
    gaps, worked = [], []
    for index, (start, end) in enumerate(intervals):
        left, right = max(start, day_start), min(end or now, now, day_end)
        if right > left:
            # Defensive interval union, so overlapping legacy rows never add time.
            if worked and left <= worked[-1][1]:
                worked[-1] = (worked[-1][0], max(worked[-1][1], right))
            else:
                worked.append((left, right))
        if not lunch_enabled or not end or not (lunch_start <= end < deadline) or end > now:
            continue
        next_start = intervals[index + 1][0] if index + 1 < len(intervals) else None
        if next_start and end < next_start < day_end:
            state, stop = "returned", next_start
        elif next_start and next_start <= end:
            continue
        elif not next_start and now < deadline:
            state, stop = "pending", now
        else:
            # Without a same-day return, the punch remains a final departure.
            state, stop = "departed", end
        gaps.append({"state": state, "start": end, "end": next_start,
                     "seconds": max(0, (stop - end).total_seconds())})

    state = gaps[-1]["state"] if gaps else "none"
    worked_seconds = sum((end - start).total_seconds() for start, end in worked)
    remaining_paid_lunch = PAID_LUNCH_SECONDS
    for gap in gaps:
        actual_seconds = gap["seconds"] if gap["state"] == "returned" else 0
        gap["paid_seconds"] = min(actual_seconds, remaining_paid_lunch)
        gap["unpaid_seconds"] = max(actual_seconds - gap["paid_seconds"], 0)
        remaining_paid_lunch -= gap["paid_seconds"]
    lunch_seconds = sum(gap["paid_seconds"] for gap in gaps)
    unpaid_lunch_seconds = sum(gap["unpaid_seconds"] for gap in gaps)
    return {
        "state": state,
        "gaps": gaps,
        "deadline": deadline,
        "worked_seconds": worked_seconds,
        "lunch_seconds": lunch_seconds,
        "unpaid_lunch_seconds": unpaid_lunch_seconds,
        "paid_seconds": worked_seconds + lunch_seconds,
        "pending_seconds": sum(gap["seconds"] for gap in gaps if gap["state"] == "pending"),
    }
