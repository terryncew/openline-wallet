"""Baseline symptom summarizer — frozen apparatus version `baseline`.

Reusable repair-process component: turns probe results into the symptom
summary the repair proposer reads on every attempt.

Rule (documented, simple): tally non-ok tasks by component; the fault hint
is the component with the most non-ok tasks (ties broken alphabetically).
"none" if every task is ok; "unknown" if there are no tasks and no absent
files; "trust" if absent files exist but no task evidence either way.
"""

VERSION = "baseline"


def summarize(probe_results, absent_files):
    lines = []
    counts = {"completed": 0, "not_completed": 0,
              "absent_files": len(absent_files)}
    tally = {}
    per_task = []
    for r in probe_results:
        if r["completed"]:
            counts["completed"] += 1
            lines.append("%s: completed, verdict=%s, component=%s, %.1fms" % (
                r["task_id"], r["verdict"], r["component"], r["runtime_ms"]))
        else:
            counts["not_completed"] += 1
            lines.append("%s: NOT completed (verdict=%s, component=%s)" % (
                r["task_id"], r["verdict"], r["component"]))
        if r["verdict"] != "ok":
            tally[r["component"]] = tally.get(r["component"], 0) + 1
        per_task.append({"task_id": r["task_id"], "verdict": r["verdict"],
                         "component": r["component"],
                         "runtime_ms": r["runtime_ms"]})
    if absent_files:
        lines.append("absent required files: %s" % ", ".join(absent_files))
    if tally:
        fault_hint = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    elif absent_files:
        fault_hint = "trust"
    elif probe_results:
        fault_hint = "none"
    else:
        fault_hint = "unknown"
    return {"version": VERSION, "lines": lines, "counts": counts,
            "fault_hint": fault_hint, "per_task": per_task}
