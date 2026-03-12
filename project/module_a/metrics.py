# ========== Metrics ==========
# This file is responsible for computing and summarizing the performance metrics of each edge in the network

import networkx as nx

# Utilization threshold at which congestion penalties begin applying
CONGESTION_THRESHOLD = 0.75

# Compute performance metrics of routing transfer
def compute_link_metrics(graph, routes):
    link_metrics = {}
    for a, b, attrs in graph.edges(data=True):
        # Order nodes alphabetically to ensure consistency for bidirectional transfers i.e B<->A ==> A<->B, A<->B ==> A<->B
        key = (min(a, b), max(a, b))
        # Initialize metrics
        link_metrics[key] = {
            "capacity_mbps"      : attrs["capacity_mbps"],
            "latency_ms"         : attrs["latency_ms"],
            "base_loss_rate"     : attrs["loss_rate"],
            "load_mbps"          : 0.0,
            "utilization"        : 0.0,
            "effective_delay_ms" : attrs["latency_ms"],
            "effective_loss_rate": attrs["loss_rate"],
        }

    # For each routed network transfer
    for route in routes:
        path = route["path"]
        size = route["size_mbps"]
        # Loop through all edges in route
        for i in range(len(path) - 1):
            # Get edge representing transfer between current and next node (alphabetically ordered to maintain consistency)
            key = (min(path[i], path[i + 1]), max(path[i], path[i + 1]))

            # Add the load size of the current transfer to the current edge's total load metric 
            if key in link_metrics:
                link_metrics[key]["load_mbps"] += size

    # For each metric in edge metrics
    for _, metrics in link_metrics.items():
        capacity    = metrics["capacity_mbps"]
        load        = metrics["load_mbps"]
        # Calculate utilization
        utilization = load / capacity
        metrics["utilization"] = round(utilization, 4)
        
        # Calculate congestion relative to utilization
        if utilization > CONGESTION_THRESHOLD:
            # calculate congestion amount and increase penalty by excess
            excess        = utilization - CONGESTION_THRESHOLD
            delay_penalty = 1.0 + (4.0 * excess)
        else:
            # no congestion, constant penalty
            delay_penalty = 1.0

        # Calculate effective delay using delay penalty
        metrics["effective_delay_ms"] = round(
            metrics["latency_ms"] * delay_penalty, 4
        )

        # Add additional loss based on excess of congestion
        if utilization > CONGESTION_THRESHOLD:
            excess     = utilization - CONGESTION_THRESHOLD
            added_loss = excess * 0.1
        else:
            added_loss = 0.0

        # Calculate effective packet loss based on congestion penalty, capped at 100% lost packets
        metrics["effective_loss_rate"] = round(
            min(metrics["base_loss_rate"] + added_loss, 1.0), 6
        )

    return link_metrics

# Summarize network metrics
def compute_network_summary(link_metrics):

    utilizations = [edge["utilization"]        for edge in link_metrics.values()]
    delays       = [edge["effective_delay_ms"]  for edge in link_metrics.values()]
    losses       = [edge["effective_loss_rate"] for edge in link_metrics.values()]
    congested    = sum(1 for u in utilizations if u > CONGESTION_THRESHOLD)

    return {
        "avg_utilization"     : round(sum(utilizations) / len(utilizations), 4),
        "max_utilization"     : round(max(utilizations), 4),
        "avg_effective_delay" : round(sum(delays) / len(delays), 4),
        "max_effective_delay" : round(max(delays), 4),
        "avg_effective_loss"  : round(sum(losses) / len(losses), 6),
        "max_effective_loss"  : round(max(losses), 6),
        "congested_links"     : congested,
        "total_links"         : len(link_metrics),
    }


if __name__ == "__main__":
    import graph as graph_module
    from traffic import generate_flows
    from routing import get_routes

    # Construct network from topology
    graph, config = graph_module.get_graph(graph_module.PATH_TOPOLOGY_CONFIG)
    # Randomly generate network transfers
    flows = generate_flows(graph, config["traffic"], tick_number=0)
    # Route transfers across network
    routes, strategy = get_routes(graph, flows)

    # Compute routing transfer metrics
    link_metrics = compute_link_metrics(graph, routes)
    # Generate summary of metrics
    summary      = compute_network_summary(link_metrics)

    print(f"Strategy : {strategy}")
    print(f"Links    : {len(link_metrics)}")
    print()
    print(f"  {'Link':<8} {'Cap':>6} {'Load':>8} {'Util':>7} {'Delay':>10} {'Loss':>8}")
    print(f"  {'-'*8} {'-'*6} {'-'*8} {'-'*7} {'-'*10} {'-'*8}")

    for (a, b), current_edge in sorted(link_metrics.items()):
        # Mark congested edges with an !
        congested = " !" if current_edge["utilization"] > CONGESTION_THRESHOLD else ""
        print(
            f"  {a}<->{b:<4} "
            f"{current_edge['capacity_mbps']:>6.0f} "
            f"{current_edge['load_mbps']:>7.1f}  "
            f"{current_edge['utilization']*100:>6.1f}% "
            f"{current_edge['effective_delay_ms']:>9.2f}ms "
            f"{current_edge['effective_loss_rate']:>8.5f}"
            f"{congested}"
        )

    print()
    print("Network Summary:")
    print(f"  Avg utilization  : {summary['avg_utilization']*100:.1f}%")
    print(f"  Max utilization  : {summary['max_utilization']*100:.1f}%")
    print(f"  Avg delay        : {summary['avg_effective_delay']:.2f} ms")
    print(f"  Max delay        : {summary['max_effective_delay']:.2f} ms")
    print(f"  Avg loss rate    : {summary['avg_effective_loss']:.5f}")
    print(f"  Max loss rate    : {summary['max_effective_loss']:.5f}")
    print(f"  Congested links  : {summary['congested_links']} / {summary['total_links']}")