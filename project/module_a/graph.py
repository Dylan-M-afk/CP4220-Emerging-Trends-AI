# ========== Graph Construction ==========
# This file is responsible for loading the topology configuration from the yaml file and using the config
# to construct an undirected graph using the nodes and edges in the network topology

import os
import yaml
import networkx as nx

PATH_TOPOLOGY_CONFIG = os.path.join(os.path.dirname(__file__), "topology.yaml")

# Read YAML topology configuration as dict
def load_config(config_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    return config

# Build NetworkX graph from config file topology
def build_graph(config):
    # Create undirected graph, all links are bi-directional 
    network_graph = nx.Graph()  

    # Add nodes to graph
    for node in config["network"]["nodes"]:
        network_graph.add_node(
            node["id"],
            label=node.get("label", node["id"])
        )

    # Add bi-directional edges linking nodes together
    for link in config["network"]["links"]:
        network_graph.add_edge(
            link["source"],
            link["target"],
            capacity_mbps=link["capacity_mbps"],
            latency_ms=link["latency_ms"],
            loss_rate=link["loss_rate"]
        )

    return network_graph

# Load network topology and build graph
def get_graph(config_path):

    config = load_config(config_path)
    graph = build_graph(config)
    return graph, config


# Test to ensure all nodes and edges loaded and connected to one another
if __name__ == "__main__":
    graph, config = get_graph(PATH_TOPOLOGY_CONFIG)

    print(f"Network : {config['network']['name']}")
    print(f"Nodes   : {graph.number_of_nodes()}")
    print(f"Edges   : {graph.number_of_edges()}")
    print()

    print("Node list:")
    for node_id, attrs in graph.nodes(data=True):
        print(f"  {node_id} — {attrs['label']}")
    print()

    print("Edge list:")
    for a, b, attrs in graph.edges(data=True):
        print(f"  {a} <-> {b}  |  capacity={attrs['capacity_mbps']} Mbps  |  latency={attrs['latency_ms']} ms  |  loss={attrs['loss_rate']}")
    print()

    # Check node connections
    if nx.is_connected(graph):
        print("Success - All nodes connected")
    else:
        # Check for nodes with no connections
        isolated = list(nx.isolates(graph))
        # Check for disconnected node groups (islands)
        components = list(nx.connected_components(graph))
        print(f"Fail - {len(components)} disconnected components")
        if isolated:
            print(f"  Isolated nodes (no links): {isolated}")