# ========== Traffic Generation ==========
# This file is responsible for randomly generating a set of network transfers between nodes with random sizes

import numpy as np

# Generate a list of random transfers for the current tick
# - graph           : graph of the current network for getting available nodes
# - traffic_config  : traffic configuration from topology.yaml
# - tick_number     : current tick count
# - return          : list of transfers in dict format
def generate_flows(graph, traffic_config, tick_number):
    # Number of transfers per tick
    num_transfers    = traffic_config["num_transfers"]
    # Range of data size for packet transfer
    min_size       = traffic_config["min_transfer_mbps"]
    max_size       = traffic_config["max_transfer_mbps"]
    base_seed        = traffic_config["seed"]

    # Use seeded random for reproducible results, offset by tick number to vary transfer properties
    rng = np.random.default_rng(base_seed + tick_number)

    # Get list of available nodes in network
    nodes = list(graph.nodes())
    flows = []
    
    for i in range(num_transfers):
        # Pick two distinct nodes at random
        source, destination = rng.choice(nodes, size=2, replace=False)

        # Assign a random transfer size within the configured range
        size_mbps = round(float(rng.uniform(min_size, max_size)), 2)

        # Save current transfer
        flows.append({
            "flow_id"     : f"tick{tick_number}_flow{i+1}",
            "source"      : str(source),
            "destination" : str(destination),
            "size_mbps"   : size_mbps
        })

    return flows

# Test flow generation
if __name__ == "__main__":
    from graph import get_graph, PATH_TOPOLOGY_CONFIG

    # Get network graph and configs
    graph, config = get_graph(PATH_TOPOLOGY_CONFIG)
    traffic_config = config["traffic"]

    # Print flows for three consecutive ticks to show variation
    for tick in range(3):
        # Generate flows for 3 ticks
        flows = generate_flows(graph, traffic_config, tick_number=tick)
        # Get total size of all transfers per tick
        total_size = sum(f["size_mbps"] for f in flows)

        print(f"Tick {tick} — {len(flows)} flows  |  total size: {total_size:.1f} Mbps")
        for flow in flows:
            print(f"  {flow['flow_id']} |\t {flow['source']} -> {flow['destination']}  {flow['size_mbps']:.2f} Mbps")
        print()