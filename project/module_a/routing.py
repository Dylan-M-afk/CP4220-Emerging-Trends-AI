# ========== Routing Algorithm ==========
# This file is responsible for determining the shortest route for transfering data across multiple nodes
# The algorithm will attempt to utilize the RL model's routing decisions by default, but if they are older than 15 seconds, missing, or invalid
# Dijkstra's algorithm will instead be used to calculate the shortest path for the transfers

import os
import json
import time
import networkx as nx


# Max time in seconds before an RL decision file is considered stale.
RL_DECISION_MAX_AGE_SECONDS = 15

# Compute shortest path for network transfers using Dijkstra's algorithm weighted by latency
# Returns transfer information with shortest path 
def shortest_path_routes(graph, flows):
    routes = []

    # Loop through each transfer
    for flow in flows:
        src  = flow["source"]
        dst  = flow["destination"]

        # Calculate shortest path for current transfer based on miliseconds of latency
        path = nx.shortest_path(
            graph,
            source=src,
            target=dst,
            weight="latency_ms"
        )

        # Save current transfer with metadata, shortest path and strategy used to calculate path
        routes.append({
            "flow_id"     : flow["flow_id"],
            "source"      : src,
            "destination" : dst,
            "path"        : path,
            "strategy"    : "dijkstra",
            "size_mbps"   : flow["size_mbps"]
        })

    return routes

# Load the RL model's shortest path decision making from a JSON file
# Returns network transfer metadata with shortest path, and strategy for pathing
def load_rl_routes(rl_decision_path, flows):
    # Check if RL routing decision file exists
    if not os.path.exists(rl_decision_path):
        return None

    # Check file is not stale using last modified date
    file_age = time.time() - os.path.getmtime(rl_decision_path)

    # Early termination for stale RL output
    if file_age > RL_DECISION_MAX_AGE_SECONDS:
        return None

    # Parse RL pathing data
    with open(rl_decision_path, "r") as f:
            rl_data = json.load(f)
    rl_paths = rl_data.get("routes", {})

    routes = []
    # Loop through each network transfer
    for flow in flows:
        fid  = flow["flow_id"]
        # Get routing path for current transfer
        path = rl_paths.get(fid)

        # Fall back to Dijkstra's shortest path for any transfer not covered by RL decision
        if not path:
            strategy = "dijkstra_fallback"
            path = nx.shortest_path(
                flow.get("_graph"),
                source=flow["source"],
                target=flow["destination"],
                weight="latency_ms"
            )
        else:
            strategy = "rl"

        # Save current transfer with metadata, shortest path and strategy for pathing
        routes.append({
            "flow_id"     : fid,
            "source"      : flow["source"],
            "destination" : flow["destination"],
            "path"        : path,
            "strategy"    : strategy,
            "size_mbps"   : flow["size_mbps"]
        })

    return routes

# Calculates shortest path for each network transfer using RL feedback where available and Dijkstra's algorithm when RL output is stale or unavailable
# Called on each tick of the simulator
def get_routes(graph, flows, rl_decision_path=None):
    # Attach graph to transfer temporarily for use in RL fallback use of Dijkstra's pathing algorithm
    for flow in flows:
        flow["_graph"] = graph

    # If rl file path exists
    if rl_decision_path:
        # Load RL pathing data
        rl_routes = load_rl_routes(rl_decision_path, flows)
        # If rl_routes are valid
        if rl_routes is not None:
            # Remove temporary graph reference 
            for flow in flows:
                flow.pop("_graph", None)
            # Return valid RL routes
            return rl_routes, "rl"

    # Remove temporary graph reference
    for flow in flows:
        flow.pop("_graph", None)

    # Use Dijkstra's algorithm for shortest path calculation of routes
    return shortest_path_routes(graph, flows), "dijkstra"


# Verify routing
if __name__ == "__main__":
    import graph as graph_module
    from traffic import generate_flows

    # Construct graph from network topology configuration
    graph, config = graph_module.get_graph(graph_module.PATH_TOPOLOGY_CONFIG)
    traffic_config = config["traffic"]

    # Randomly generate network transfers based on traffic configuration
    flows = generate_flows(graph, traffic_config, tick_number=0)
    # Get shortest path for each transfer using RL if available, otherwise use Dijkstra's algorithm
    routes, strategy = get_routes(graph, flows)

    print(f"Strategy : {strategy}")
    print(f"Routes   : {len(routes)}")
    print()

    # Loop through each route
    for route in routes:
        path_str = " -> ".join(route["path"]) if route["path"] else "NO PATH"
        # Calculate how many nodes must be traversed
        hops = len(route["path"]) - 1 if len(route["path"]) > 1 else 0

        # Calculate total latency across all transfers
        total_latency = 0.0
        # For N nodes there are N-1 links
        for i in range(len(route["path"]) - 1):
            # Get attributes of current and next node in the path
            edge_data = graph[route["path"][i]][route["path"][i+1]]
            # Sum latency of current transfer path
            total_latency += edge_data["latency_ms"]

        print(f"  {route['flow_id']:20s} | {path_str:30s} | {hops} hops | latency={total_latency:.0f}ms | {route['size_mbps']:.2f} Mbps")