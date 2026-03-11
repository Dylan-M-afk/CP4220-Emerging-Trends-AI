import os
import json
import time
import networkx as nx


# Max time in seconds before an RL decision file is considered stale.
# If Module B hasn't written a fresh decision within this window,
# the simulator falls back to shortest path (Djikstra) routing.
RL_DECISION_MAX_AGE_SECONDS = 15

# Compute shortest path for flows using Dijkstra's algorithm weighted by latency
# Returns flow information with shortest path 
def shortest_path_routes(graph, flows):
    routes = []

    # Loop through each flow
    for flow in flows:
        src  = flow["source"]
        dst  = flow["destination"]

        # Calculate shortest path for current flow
        path = nx.shortest_path(
            graph,
            source=src,
            target=dst,
            weight="latency_ms"
        )

        # Save current flow with shortest path and strategy used to calculate path
        routes.append({
            "flow_id"     : flow["flow_id"],
            "source"      : src,
            "destination" : dst,
            "path"        : path,
            "strategy"    : "shortest_path",
            "size_mbps"   : flow["size_mbps"]
        })

    return routes

# Load RL Model's routing decision from a JSON file
# Returns flow information with shortest path
def load_rl_routes(rl_decision_path, flows):
    # Check if RL routing decision file exists
    if not os.path.exists(rl_decision_path):
        return None

    # Check file is not stale
    file_age = time.time() - os.path.getmtime(rl_decision_path)
    if file_age > RL_DECISION_MAX_AGE_SECONDS:
        return None

    # Load and parse
    try:
        with open(rl_decision_path, "r") as f:
            rl_data = json.load(f)
        rl_paths = rl_data.get("routes", {})
    except (json.JSONDecodeError, KeyError):
        return None

    routes = []
    for flow in flows:
        fid  = flow["flow_id"]
        # Get routing path for current flow
        path = rl_paths.get(fid)

        # Fall back to shortest path for any flow not covered by RL decision
        if not path:
            strategy = "shortest_path_fallback"
            path = nx.shortest_path(
                flow.get("_graph"),
                source=flow["source"],
                target=flow["destination"],
                weight="latency_ms"
            )
        else:
            strategy = "rl"

        # Save current flow with shortest path and strategy
        routes.append({
            "flow_id"     : fid,
            "source"      : flow["source"],
            "destination" : flow["destination"],
            "path"        : path,
            "strategy"    : strategy,
            "size_mbps"   : flow["size_mbps"]
        })

    return routes

# Calculates shortest path for each flow
# Called on each tick of the simulator
# Attempts to use RL flow path if available, otherwsie Djikstraw's algorithm is used for shortest path
def get_routes(graph, flows, rl_decision_path=None):
    # Attach graph to flows temporarily for RL fallback lookups
    for flow in flows:
        flow["_graph"] = graph

    # If rl path exists
    if rl_decision_path:
        rl_routes = load_rl_routes(rl_decision_path, flows)
        # If rl_routes are valid
        if rl_routes is not None:
            # Clean up temporary graph reference before returning
            for flow in flows:
                flow.pop("_graph", None)
            return rl_routes, "rl"

    # Clean up temporary graph reference
    for flow in flows:
        flow.pop("_graph", None)

    return shortest_path_routes(graph, flows), "shortest_path"


# Verify routing
if __name__ == "__main__":
    import graph as graph_module
    from traffic import generate_flows

    graph, config = graph_module.get_graph(graph_module.PATH_TOPOLOGY_CONFIG)
    traffic_config = config["traffic"]

    flows = generate_flows(graph, traffic_config, tick_number=0)
    routes, strategy = get_routes(graph, flows)

    print(f"Strategy : {strategy}")
    print(f"Routes   : {len(routes)}")
    print()

    for route in routes:
        path_str = " -> ".join(route["path"]) if route["path"] else "NO PATH"
        hops     = len(route["path"]) - 1 if len(route["path"]) > 1 else 0

        # Sum latency along the path
        total_latency = 0.0
        for i in range(len(route["path"]) - 1):
            edge_data      = graph[route["path"][i]][route["path"][i+1]]
            total_latency += edge_data["latency_ms"]

        print(
            f"  {route['flow_id']:20s} | "
            f"{path_str:30s} | "
            f"{hops} hops | "
            f"latency={total_latency:.0f}ms | "
            f"{route['size_mbps']:.2f} Mbps"
        )