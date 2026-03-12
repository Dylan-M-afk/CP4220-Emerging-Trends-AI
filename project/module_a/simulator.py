# ========== Simulator ==========
#
# Each tick:
#   1. Generate random transfers for this tick
#   2. Route transfers using RL decisions if available, otherwise Dijkstra
#   3. Compute per-link and network-wide metrics
#   4. Write state.json and append to history.csv
#   5. Sleep until the next tick

import os
import sys
import time
import json
import shutil
import signal

import graph as graph_module
from traffic      import generate_flows
from routing      import get_routes
from metrics      import compute_link_metrics, compute_network_summary
from state_writer import write_all

# Construct required paths to config file and shared log directory

MODULE_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = graph_module.PATH_TOPOLOGY_CONFIG

def resolve_shared_dir(sim_config):
    raw = sim_config.get("shared", "../shared")
    return os.path.abspath(os.path.join(MODULE_DIR, raw))

def rl_decision_path(shared_dir):
    return os.path.join(shared_dir, "routing_decision.json")



# Write state to json file using temp file to prevent reading of partial values
def atomic_write_state(shared_dir, state):
    final_path = os.path.join(shared_dir, "state.json")
    temp_path  = os.path.join(shared_dir, "state.tmp.json")

    # write to temp path
    with open(temp_path, "w") as f:
        json.dump(state, f, indent=2)

    # move temporary file to final location after writing completed
    shutil.move(temp_path, final_path)

def main():
    # Generate network graph from topology config
    graph, config = graph_module.get_graph(CONFIG_PATH)

    # obtain traffic and simulation configurations
    traffic_cfg   = config["traffic"]
    sim_cfg       = config["simulation"]
    # get tick configuration parameters
    tick_interval = sim_cfg.get("tick_interval_seconds", 5)
    max_ticks     = sim_cfg.get("max_ticks", None)
    # get paths of log-file directory and RL-model output
    shared_dir    = resolve_shared_dir(sim_cfg)
    rl_path       = rl_decision_path(shared_dir)

    # verify shared log directory exists
    os.makedirs(shared_dir, exist_ok=True)

    print(f"Network   : {config['network']['name']}")
    print(f"Nodes     : {graph.number_of_nodes()}  |  Links: {graph.number_of_edges()}")
    print(f"Tick rate : every {tick_interval}s")
    print(f"Max ticks : {max_ticks if max_ticks else 'unlimited'}")
    print(f"Shared dir: {shared_dir}")
    print()
    print("Starting simulation — press Ctrl+C to stop")
    print("-" * 60)

    # Simulation runs while active
    running = {"active": True}
    tick = 0

    # Graceful shutdown on Ctrl+C or SIGTERM
    def handle_shutdown(sig, frame):
        print("\nShutdown signal received — finishing current tick then stopping...")
        running["active"] = False

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)


    while running["active"]:
        if max_ticks is not None and tick >= max_ticks:
            print(f"Reached max_ticks ({max_ticks}) — stopping.")
            break

        tick_start = time.time()

        try:
            # Randomly generate network transfers
            flows = generate_flows(graph, traffic_cfg, tick_number=tick)

            # Route transfers across nodes
            routes, strategy = get_routes(graph, flows, rl_decision_path=rl_path)

            # Compute metrics of edges and generate summary of metrics
            link_metrics = compute_link_metrics(graph, routes)
            summary      = compute_network_summary(link_metrics)

            # Log simulator state and tick history
            write_all(shared_dir, tick, strategy, flows, routes, link_metrics, summary)

            print( f"Tick {tick:>4} | strategy={strategy:<20} | util={summary['avg_utilization']*100:>5.1f}% avg  {summary['max_utilization']*100:>5.1f}% max | \
                  congested={summary['congested_links']}/{summary['total_links']} | delay={summary['avg_effective_delay']:.1f}ms avg")

        except Exception as e:
            print(f"Tick {tick:>4} | ERROR — {e}")

        # Sleep for remainder of tick interval
        elapsed    = time.time() - tick_start
        # Calculate remaining time
        sleep_time = max(0.0, tick_interval - elapsed)
        time.sleep(sleep_time)

        # Continue to next tick
        tick += 1

    print("-" * 60)
    print(f"Simulation stopped after {tick} ticks.")


if __name__ == "__main__":
    main()