# ========== Offline Trainer ==========
# This file is responsible for pre-training the Q-table from historical simulation data in history.csv
# Run this before starting rl_agent.py to give the agent a warm-start rather than training from scratch

import os
import json
import csv
import pickle
import numpy as np
import networkx as nx
import yaml

# ─── Paths ────────────────────────────────────────────────────────────────────

MODULE_DIR   = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR   = os.path.abspath(os.path.join(MODULE_DIR, "../shared"))
TOPOLOGY_PATH = os.path.abspath(os.path.join(MODULE_DIR, "../module_a/topology.yaml"))
HISTORY_PATH  = os.path.join(SHARED_DIR, "history.csv")
MODEL_PATH    = os.path.join(MODULE_DIR,  "qtable.pkl")

# ─── Hyper-parameters ─────────────────────────────────────────────────────────

ALPHA              = 0.3    # Learning rate — how much each new sample shifts the Q-value
GAMMA              = 0.9    # Discount factor — how much future rewards matter
CONGESTION_THRESH  = 0.75   # Mirror of metrics.py CONGESTION_THRESHOLD
UTIL_BINS          = 5      # Number of discretisation buckets per link (0-20%, 20-40%, …)

# ─── Graph helpers ────────────────────────────────────────────────────────────

def load_graph():
    """Load the NetworkX graph and all simple paths from topology.yaml."""
    with open(TOPOLOGY_PATH) as f:
        config = yaml.safe_load(f)

    G = nx.Graph()
    for node in config["network"]["nodes"]:
        G.add_node(node["id"], label=node.get("label", node["id"]))
    for link in config["network"]["links"]:
        G.add_edge(
            link["source"], link["target"],
            capacity_mbps=link["capacity_mbps"],
            latency_ms=link["latency_ms"],
            loss_rate=link["loss_rate"],
        )
    return G, config

def get_all_paths(G):
    """
    Pre-compute every simple (loop-free) path between every ordered node pair.
    Returns a dict: {(src, dst): [[node, node, …], …]}
    Paths are sorted by total latency (shortest first) so action index 0 is always
    the Dijkstra baseline — the RL agent only needs to beat index 0.
    """
    nodes = sorted(G.nodes())
    all_paths = {}
    for src in nodes:
        for dst in nodes:
            if src == dst:
                continue
            try:
                paths = list(nx.all_simple_paths(G, src, dst, cutoff=6))
            except nx.NetworkXNoPath:
                paths = []

            # Sort by total base latency so action 0 == Dijkstra
            def path_latency(p):
                return sum(G[p[i]][p[i+1]]["latency_ms"] for i in range(len(p)-1))

            paths.sort(key=path_latency)
            all_paths[(src, dst)] = paths
    return all_paths

def get_link_order(G):
    """Return a stable ordered list of (min_node, max_node) edge tuples."""
    return sorted((min(a, b), max(a, b)) for a, b in G.edges())

# ─── State discretisation ─────────────────────────────────────────────────────

def discretise_utilization(util: float) -> int:
    """Map a float utilization in [0, 1+] to an integer bucket in [0, UTIL_BINS-1]."""
    return min(int(util * UTIL_BINS), UTIL_BINS - 1)

def build_state_key(link_utils: dict, link_order: list) -> tuple:
    """
    Convert the {edge_key: utilization} dict into a hashable tuple of int bins.
    link_order guarantees the same tuple layout every call.
    """
    return tuple(discretise_utilization(link_utils.get(lk, 0.0)) for lk in link_order)

# ─── Reward function ──────────────────────────────────────────────────────────

def compute_reward(avg_utilization: float, avg_delay: float, congested_links: int, total_links: int) -> float:
    """
    Reward mirrors the RL agent's objective from the concept doc:
      - penalise average end-to-end delay
      - penalise links over the congestion threshold
    Both penalties are normalised so the reward is roughly in [-10, 0].
    """
    congestion_fraction = congested_links / max(total_links, 1)
    delay_penalty       = avg_delay / 100.0          # normalise ms → ~[0, 1] range
    util_penalty        = congestion_fraction * 2.0  # weight congestion more heavily
    return -(delay_penalty + util_penalty)

# ─── Q-table helpers ──────────────────────────────────────────────────────────

def load_or_init_qtable(model_path: str, all_paths: dict) -> dict:
    """Load an existing Q-table from disk, or initialise a fresh one."""
    if os.path.exists(model_path):
        with open(model_path, "rb") as f:
            qtable = pickle.load(f)
        print(f"Loaded existing Q-table from {model_path}")
    else:
        # Initialise with zeros; keys added on demand during training
        qtable = {}
        print("Initialised fresh Q-table")
    return qtable

def q_get(qtable: dict, state: tuple, num_actions: int) -> np.ndarray:
    """Return the Q-values for a state, creating the row with zeros if missing."""
    if state not in qtable:
        qtable[state] = np.zeros(num_actions)
    return qtable[state]

def q_update(qtable, state, action, reward, next_state, num_actions, alpha=ALPHA, gamma=GAMMA):
    """Standard Bellman Q-update."""
    q_now  = q_get(qtable, state, num_actions)[action]
    q_next = np.max(q_get(qtable, next_state, num_actions))
    q_get(qtable, state, num_actions)[action] = q_now + alpha * (reward + gamma * q_next - q_now)

def save_qtable(qtable: dict, model_path: str):
    with open(model_path, "wb") as f:
        pickle.dump(qtable, f)
    print(f"Q-table saved → {model_path}  ({len(qtable)} states)")

# ─── CSV helpers ──────────────────────────────────────────────────────────────

def load_history(history_path: str) -> list[dict]:
    """Read history.csv and return a list of row dicts (newest last)."""
    if not os.path.exists(history_path):
        return []
    with open(history_path, newline="") as f:
        return list(csv.DictReader(f))

# ─── Training loop ────────────────────────────────────────────────────────────

def train(rows: list[dict], qtable: dict, all_paths: dict, link_order: list):
    """
    Offline training pass over history.csv rows.

    Because history.csv does not store per-link utilization (only summary stats),
    we approximate the state from the aggregate utilization columns.
    The RL agent refines this online from the richer state.json.

    Strategy:
      For each consecutive pair of rows (s_t, s_{t+1}):
        - Build a proxy state from avg/max utilization bins
        - Choose action 0 (baseline path) for the state seen in the CSV
          since we don't know what path Module A actually used per flow
        - Update Q-values with the observed reward at s_{t+1}
    """
    if len(rows) < 2:
        print("Not enough history rows to train (need ≥ 2).")
        return 0

    # Use a single canonical (src, dst) pair to represent aggregate network quality.
    # This simplified proxy lets the Q-table learn the mapping:
    #   network_congestion_level → prefer less-loaded paths
    # The online agent uses the full per-link state instead.
    PROXY_PAIR = None
    for pair, paths in all_paths.items():
        if len(paths) >= 2:
            PROXY_PAIR = pair
            break

    if PROXY_PAIR is None:
        print("No (src, dst) pair with ≥ 2 paths found — cannot train.")
        return 0

    num_actions = len(all_paths[PROXY_PAIR])
    updates = 0

    for i in range(len(rows) - 1):
        row_t  = rows[i]
        row_t1 = rows[i + 1]

        try:
            avg_util_t  = float(row_t["avg_utilization"])
            max_util_t  = float(row_t["max_utilization"])
            avg_util_t1 = float(row_t1["avg_utilization"])
            max_util_t1 = float(row_t1["max_utilization"])
            avg_delay   = float(row_t1["avg_effective_delay"])
            congested   = int(row_t1["congested_links"])
            total_links = int(row_t1["total_links"])
        except (KeyError, ValueError):
            continue

        # Proxy state: two buckets — avg and max utilization
        state      = (discretise_utilization(avg_util_t),  discretise_utilization(max_util_t))
        next_state = (discretise_utilization(avg_util_t1), discretise_utilization(max_util_t1))

        reward     = compute_reward(avg_util_t1, avg_delay, congested, total_links)

        # Greedy action selection during offline training
        action = int(np.argmax(q_get(qtable, state, num_actions)))
        q_update(qtable, state, action, reward, next_state, num_actions)
        updates += 1

    return updates

# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Module B — Offline Q-table Trainer")
    print("=" * 60)

    # Load graph and pre-compute all paths
    G, _ = load_graph()
    print(f"Graph loaded: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    all_paths  = get_all_paths(G)
    link_order = get_link_order(G)
    total_pairs = sum(1 for paths in all_paths.values() if paths)
    print(f"Pre-computed paths for {total_pairs} (src, dst) pairs")

    # Load or initialise Q-table
    qtable = load_or_init_qtable(MODEL_PATH, all_paths)

    # Load history
    rows = load_history(HISTORY_PATH)
    if not rows:
        print(f"\nNo history found at {HISTORY_PATH}")
        print("Run the simulator first to generate training data, then re-run train.py")
        return

    print(f"\nLoaded {len(rows)} history rows from {HISTORY_PATH}")

    # Train
    updates = train(rows, qtable, all_paths, link_order)
    print(f"Training complete — {updates} Q-table updates across {len(rows)} rows")
    print(f"Q-table now covers {len(qtable)} distinct states")

    # Save
    save_qtable(qtable, MODEL_PATH)
    print("\nDone. Start rl_agent.py to use this Q-table for live routing.")
    print("=" * 60)


if __name__ == "__main__":
    main()
