# ========== Simulator ==========
# This file is responsible for running the simulator, 
# it generates a network and data transfers, routes the transfers through the network then computes and logs performance metrics and state for transfers

import os
import time
import signal

import graph as graph_module
from traffic      import generate_flows
from routing      import get_routes
from metrics      import compute_link_metrics, compute_network_summary
from state_writer import write_all

# Construct required paths to config files and shared log directory

MODULE_DIR  = os.path.dirname(os.path.abspath(__file__))
TOPOLOGY_CONFIG_PATH = graph_module.PATH_TOPOLOGY_CONFIG
NETWORK_CONFIG_PATH = graph_module.PATH_NETWORK_CONFIG

def resolve_shared_dir(sim_config):
    raw = sim_config.get("shared", "../shared")
    return os.path.abspath(os.path.join(MODULE_DIR, raw))

def rl_decision_path(shared_dir):
    return os.path.join(shared_dir, "routing_decision.json")

# Check network parameter configuration for current parameters
# Parameters are expected to be updated during simulation by Module C
def poll_network_parameter_config(config_path=NETWORK_CONFIG_PATH):
    network_parameter_config = graph_module.get_network_parameters(config_path)

    # obtain traffic and simulation configurations
    traffic_cfg = network_parameter_config["traffic"]
    sim_cfg = network_parameter_config["simulation"]
    # get tick configuration parameters
    tick_interval = sim_cfg.get("tick_interval_seconds", 5)
    max_ticks = sim_cfg.get("max_ticks", None)
    use_rl = sim_cfg.get("use_rl", None)
    # get paths of log-file directory and RL-model output
    shared_dir = resolve_shared_dir(sim_cfg)
    rl_path = rl_decision_path(shared_dir)

    return traffic_cfg, tick_interval, max_ticks, shared_dir, rl_path, use_rl

def main():
    # Generate network graph from topology config
    graph, topology_config = graph_module.get_graph(TOPOLOGY_CONFIG_PATH)
    
    # Get network parameters from parameter config
    traffic_cfg, tick_interval, max_ticks, shared_dir, rl_path, use_rl = poll_network_parameter_config(NETWORK_CONFIG_PATH)

    # verify shared log directory exists
    os.makedirs(shared_dir, exist_ok=True)

    print(f"Network   : {topology_config['network']['name']}")
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
            # Poll network parameters config to check for updates from Module C
            traffic_cfg, tick_interval, max_ticks, shared_dir, rl_path, use_rl = poll_network_parameter_config(NETWORK_CONFIG_PATH)

            # Randomly generate network transfers
            flows = generate_flows(graph, traffic_cfg, tick_number=tick)

            # Route transfers across nodes
            routes, strategy = get_routes(graph, flows, use_rl, rl_decision_path=rl_path)

            # Compute metrics of edges and generate summary of metrics
            link_metrics = compute_link_metrics(graph, routes)
            summary = compute_network_summary(link_metrics)

            # Log simulator state and tick history
            write_all(shared_dir, tick, strategy, flows, routes, link_metrics, summary)

            print( f"Tick {tick:>4} | strategy={strategy:<20} | util={summary['avg_utilization']*100:>5.1f}% avg  {summary['max_utilization']*100:>5.1f}% max | \
                  congested={summary['congested_links']}/{summary['total_links']} | delay={summary['avg_effective_delay']:.1f}ms avg")

        except Exception as e:
            print(f"Tick {tick:>4} | ERROR — {e}")

        # Sleep for remainder of tick interval
        elapsed = time.time() - tick_start
        # Calculate remaining time
        sleep_time = max(0.0, tick_interval - elapsed)
        time.sleep(sleep_time)

        # Continue to next tick
        tick += 1

    print("-" * 60)
    print(f"Simulation stopped after {tick} ticks.")


if __name__ == "__main__":
    main()