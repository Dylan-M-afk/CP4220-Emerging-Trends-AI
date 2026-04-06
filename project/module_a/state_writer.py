# ========== State Writer ==========
# This file is responsible for saving the final state of the simulation and the history of all ticks which occured during the simulation

import os
import json
import csv
import shutil
from datetime import datetime, timezone

CSV_BASE_COLUMNS = [
    "tick",
    "timestamp",
    "strategy",
    "num_flows",
    "total_load_mbps",
    "avg_utilization",
    "max_utilization",
    "avg_effective_delay",
    "max_effective_delay",
    "avg_effective_loss",
    "max_effective_loss",
    "congested_links",
    "total_links",
]

def _link_columns(link_metrics: dict) -> list[str]:
    """
    Return a stable sorted list of per-link column names derived from
    link_metrics keys, e.g. ["link_util_A<->B", "link_util_A<->F", …].
    Sorting ensures every row uses the same column order.
    """
    return [f"link_util_{a}<->{b}" for (a, b) in sorted(link_metrics.keys())]

def _all_columns(link_metrics: dict) -> list[str]:
    return CSV_BASE_COLUMNS + _link_columns(link_metrics)

# Write state of edge to json for logging
def write_state(shared_dir, tick_number, strategy, flows, routes, link_metrics, summary):
    serialized_links = {}

    # Serialize edges of link_metrics using tuple keys as strings for JSON
    for (a, b), metrics in link_metrics.items():
        serialized_links[f"{a}<->{b}"] = metrics

    state = {
        "tick"        : tick_number,
        "timestamp"   : datetime.now(timezone.utc).isoformat(),
        "strategy"    : strategy,
        "flows"       : flows,
        "routes"      : routes,
        "link_metrics": serialized_links,
        "summary"     : summary,
    }

    # Write current state to state.json using a temp file to avoid reading partial files
    final_path = os.path.join(shared_dir, "state.json")
    temp_path  = os.path.join(shared_dir, "state.tmp.json")
    with open(temp_path, "w") as f:
        json.dump(state, f, indent=2)
    shutil.move(temp_path, final_path)

# Track tick history across simulation
def append_history(shared_dir, tick_number, strategy, flows, summary, link_metrics):
    path       = os.path.join(shared_dir, "history.csv")
    # Check if this is the first tick based on presence of history file
    first_tick = not os.path.exists(path)
    # Calculate total size of all data transferred during current tick
    total_load = round(sum(f["size_mbps"] for f in flows), 2)

    # Build per-link utilization columns
    link_util_row = {
        f"link_util_{a}<->{b}": metrics["utilization"]
        for (a, b), metrics in link_metrics.items()
    }

    all_columns = _all_columns(link_metrics)

    row = {
        "tick"               : tick_number,
        "timestamp"          : datetime.now(timezone.utc).isoformat(),
        "strategy"           : strategy,
        "num_flows"          : len(flows),
        "total_load_mbps"    : total_load,
        "avg_utilization"    : summary["avg_utilization"],
        "max_utilization"    : summary["max_utilization"],
        "avg_effective_delay": summary["avg_effective_delay"],
        "max_effective_delay": summary["max_effective_delay"],
        "avg_effective_loss" : summary["avg_effective_loss"],
        "max_effective_loss" : summary["max_effective_loss"],
        "congested_links"    : summary["congested_links"],
        "total_links"        : summary["total_links"],
        **link_util_row,
    }

    # Append current tick data to history
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_columns)
        if first_tick:
            # Add column headers before tick row if first tick of simulation
            writer.writeheader()
        writer.writerow(row)

# Log edge state and tick history
def write_all(shared_dir, tick_number, strategy, flows, routes, link_metrics, summary):
    # Verify path
    os.makedirs(shared_dir, exist_ok=True)

    # Log to files
    write_state(shared_dir, tick_number, strategy, flows, routes, link_metrics, summary)
    # FIX: pass link_metrics through so per-link utils are persisted in history.csv
    append_history(shared_dir, tick_number, strategy, flows, summary, link_metrics)


if __name__ == "__main__":
    import csv as csv_module
    import graph as graph_module
    from traffic import generate_flows
    from routing import get_routes
    from metrics import compute_link_metrics, compute_network_summary

    # Generate network from topology
    graph, config = graph_module.get_graph(graph_module.PATH_TOPOLOGY_CONFIG)
    # Get directory containing log files
    shared_dir    = os.path.join(os.path.dirname(__file__), "../shared")
    # Get traffic config
    traffic_cfg   = config["traffic"]

    # Simulate 3 ticks
    for tick in range(3):
        # Generate transfers
        flows = generate_flows(graph, traffic_cfg, tick_number=tick)
        # Route transfers
        routes, strategy = get_routes(graph, flows)
        # Compute edge transfer metrics
        link_metrics = compute_link_metrics(graph, routes)
        # Generate summary of edge metrics
        summary = compute_network_summary(link_metrics)

        # Log edge state and tick data
        write_all(shared_dir, tick, strategy, flows, routes, link_metrics, summary)
        print(f"Tick {tick} written — strategy={strategy}  congested={summary['congested_links']}/{summary['total_links']}  max_util={summary['max_utilization']*100:.1f}%")

    # Read and print results of log files
    print()
    state_path = os.path.join(shared_dir, "state.json")
    with open(state_path) as f:
        state = json.load(f)
    print(f"state.json  — tick={state['tick']}  links={len(state['link_metrics'])}  flows={len(state['flows'])}")

    history_path = os.path.join(shared_dir, "history.csv")
    with open(history_path) as f:
        rows = list(csv_module.DictReader(f))
    print(f"history.csv — {len(rows)} rows  columns={list(rows[0].keys())}")