from pathlib import Path

from pyinfra import host, local, state

from group_data.features import DEPLOY, validate

# Each host's FEATURES set (from group_data/<group>.py) selects which task
# bundles run on it. raspi declares every feature, so its deploy is unchanged;
# raspo (camera node) declares only {base, camera}. See group_data/features.py.
_features = set(host.data.get("FEATURES") or ())
validate(_features)

_tasks_dir = Path(__file__).parent / "tasks"

for _i, (_task, _feature) in enumerate(DEPLOY):
    if _feature not in _features:
        continue
    # A feature may be declared before its task files exist (e.g. `camera`
    # while ocular is still being built). Skip-with-warning rather than crash,
    # so a half-built feature is non-blocking and auto-activates once its files
    # land. Typos still surface here as a visible skip.
    if not (_tasks_dir / f"{_task}.py").is_file():
        print(f"[deploy] feature '{_feature}': tasks/{_task}.py not present yet — skipping")
        continue
    # Every local.include() here shares one call site (this line), so pyinfra's
    # operation-order key would otherwise be identical for all tasks and ops
    # would sort purely by line number *within* each task file — collapsing the
    # DEPLOY manifest order. Across two hosts running different task subsets that
    # yields contradictory partial orders and a "Cycle detected in operation
    # ordering DAG" error. current_op_file_number is pyinfra's primary order key
    # (it normally distinguishes CLI files); bumping it per task restores each
    # task as a contiguous block in manifest order.
    state.current_op_file_number = _i
    local.include(f"tasks/{_task}.py")
