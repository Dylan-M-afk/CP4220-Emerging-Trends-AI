# ========== RL Agent ==========
# This file is responsible for the online reinforcement learning agent.
# Every 5 seconds it reads /shared/state.json, selects an optimal path for each active
# flow using a Q-table, and writes the decision to /shared/routing_decision.json.
# Module A picks up routing_decision.json on its next tick and applies the RL paths.
#
# Run order:
#   1. python train.py          (optional warm-start from history.csv)
#   2. python rl_agent.py       (live agent loop)

import os
import json
import time
import shutil
import pickle
import signal
import numpy as np
import networkx as nx
import yaml

# ─── Paths ────────────────────────────────────────────────────────────────────

MODULE_DIR    = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR    = os.path.abspath(os.path.join(MODULE_DIR, "../shared"))
TOPOLOGY_PATH = os.path.abspath(os.path.join(MODULE_DIR, "../module_a/topology.yaml"))
STATE_PATH    = os.path.join(SHARED_DIR, "routing_decision.json")
DECISION_PATH = os.path.join(SHARED_DIR, "routing_decision.json")
MODEL_PATH    = os.path.join(MODULE_DIR,  "qtable.pkl")

# ─── Hyper-parameters ─────────────────────────────────────────────────────────

TICK_INTERVAL     = float(os.environ.get("RL_TICK_INTERVAL", 5))  # synced from run.py, or default 5s
ALPHA             = 0.1    # online learning rate (lower than offline to avoid oscillation)
GAMMA             = 0.9    # discount factor
EPSILON_START     = 0.2    # initial exploration rate
EPSILON_MIN       = 0.02   # floor — always keep a tiny chance of exploration
EPSILON_DECAY     = 0.995  # multiplicative decay applied each tick
CONGESTION_THRESH = 0.75   # must match metrics.py
UTIL_BINS         = 5      # must match train.py

# ─── Graph helpers ────────────────────────────────────────────────────────────

def load_graph():
    """Load the NetworkX graph from topology.yaml (same as Module A)."""
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
    return G

def get_all_paths(G):
    """
    Pre-compute every simple (loop-free) path between every ordered node pair.
    Paths are sorted by total base latency so action index 0 == Dijkstra baseline.
    Cutoff of 6 keeps the action space tractable on the 11-node topology.
    Returns: {(src, dst): [[node, …], …]}
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

            def path_latency(p):
                return sum(G[p[i]][p[i+1]]["latency_ms"] for i in range(len(p)-1))

            paths.sort(key=path_latency)
            all_paths[(src, dst)] = paths
    return all_paths

def get_link_order(G):
    """Stable ordered list of (min_node, max_node) edge tuples — matches train.py."""
    return sorted((min(a, b), max(a, b)) for a, b in G.edges())

# ─── State helpers ────────────────────────────────────────────────────────────

def discretise_utilization(util: float) -> int:
    """Map float utilization → integer bin. Matches train.py."""
    return min(int(util * UTIL_BINS), UTIL_BINS - 1)

def build_state_key(link_metrics: dict, link_order: list) -> tuple:
    """
    Convert the link_metrics dict from state.json into a hashable state tuple.
    Each element is the discretised utilization of the corresponding link.

    state.json stores edges as "A<->B" strings with a 'utilization' field.
    link_order uses (min, max) tuples — we convert to match.
    """
    key_map = {}
    for edge_str, metrics in link_metrics.items():
        parts = edge_str.split("<->")
        if len(parts) == 2:
            a, b = parts
            key_map[(min(a, b), max(a, b))] = metrics.get("utilization", 0.0)

    return tuple(discretise_utilization(key_map.get(lk, 0.0)) for lk in link_order)

# ─── Reward ───────────────────────────────────────────────────────────────────

def compute_reward(summary: dict) -> float:
    """
    Reward function matching the concept doc objective:
      - penalise average delay
      - penalise links over the congestion threshold
    Returns a float in roughly [-10, 0]; higher is better.
    """
    avg_delay        = summary.get("avg_effective_delay", 0.0)
    congested        = summary.get("congested_links", 0)
    total_links      = summary.get("total_links", 1)
    congestion_frac  = congested / max(total_links, 1)
    delay_penalty    = avg_delay / 100.0
    util_penalty     = congestion_frac * 2.0
    return -(delay_penalty + util_penalty)

# ─── Q-table helpers ──────────────────────────────────────────────────────────

def load_or_init_qtable(model_path: str) -> dict:
    if os.path.exists(model_path):
        with open(model_path, "rb") as f:
            qtable = pickle.load(f)
        print(f"Loaded Q-table from {model_path}  ({len(qtable)} states)")
    else:
        qtable = {}
        print("No Q-table found — starting from scratch (consider running train.py first)")
    return qtable

def save_qtable(qtable: dict, model_path: str):
    with open(model_path, "wb") as f:
        pickle.dump(qtable, f)

def q_get(qtable: dict, state: tuple, num_actions: int) -> np.ndarray:
    if state not in qtable:
        qtable[state] = np.zeros(num_actions)
    return qtable[state]

def q_update(qtable, state, action, reward, next_state, num_actions):
    q_now  = q_get(qtable, state, num_actions)[action]
    q_next = np.max(q_get(qtable, next_state, num_actions))
    q_get(qtable, state, num_actions)[action] += ALPHA * (reward + GAMMA * q_next - q_now)

# ─── Action selection ─────────────────────────────────────────────────────────

def select_action(qtable: dict, state: tuple, num_actions: int, epsilon: float) -> int:
    """Epsilon-greedy selection — explore randomly or exploit the best known action."""
    if np.random.random() < epsilon:
        return np.random.randint(num_actions)
    return int(np.argmax(q_get(qtable, state, num_actions)))

# ─── Decision file helpers ────────────────────────────────────────────────────

def read_state(state_json_path: str) -> dict | None:
    """
    Read /shared/state.json written by Module A.
    Returns parsed dict or None if the file is missing/corrupt.
    Module A writes atomically via a temp file so partial reads are very unlikely,
    but we guard anyway.
    """
    if not os.path.exists(state_json_path):
        return None
    try:
        with open(state_json_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

def write_decision(decision_path: str, routes: dict, tick: int):
    """
    Write routing_decision.json atomically so Module A never reads a partial file.
    Schema that Module A's routing.py expects:
        { "routes": { "<flow_id>": ["NodeA", "NodeB", …], … } }
    """
    payload = {
        "tick"  : tick,
        "routes": routes,         # {flow_id: [node, node, …]}
    }
    tmp_path = decision_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
    shutil.move(tmp_path, decision_path)

# ─── Per-tick logic ───────────────────────────────────────────────────────────

def decide_routes(
    state_data : dict,
    all_paths  : dict,
    link_order : list,
    qtable     : dict,
    epsilon    : float,
    G          : nx.Graph,
) -> tuple[dict, tuple]:
    """
    Given the current state.json payload, select a path for every active flow.

    Returns:
        routes  — {flow_id: [node, …]}  ready to write to routing_decision.json
        state_key — the discretised state tuple used for this decision (for Q-update)
    """
    link_metrics = state_data.get("link_metrics", {})
    flows        = state_data.get("flows", [])
    state_key    = build_state_key(link_metrics, link_order)

    routes = {}
    for flow in flows:
        src = flow["source"]
        dst = flow["destination"]
        fid = flow["flow_id"]

        paths = all_paths.get((src, dst), [])

        if not paths:
            # No pre-computed path — fall back to Dijkstra (Module A will also do this,
            # but being explicit here keeps our route dict complete)
            try:
                path = nx.shortest_path(G, src, dst, weight="latency_ms")
            except nx.NetworkXNoPath:
                path = []
            routes[fid] = path
            continue

        num_actions = len(paths)
        action      = select_action(qtable, state_key, num_actions, epsilon)
        routes[fid] = paths[action]

    return routes, state_key

# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Module B — RL Agent (online Q-learning)")
    print("=" * 60)

    # Load topology
    G          = load_graph()
    all_paths  = get_all_paths(G)
    link_order = get_link_order(G)
    print(f"Graph: {G.number_of_nodes()} nodes  {G.number_of_edges()} edges")
    print(f"Shared dir: {SHARED_DIR}")
    print(f"State file: {os.path.join(SHARED_DIR, 'state.json')}")
    print(f"Decision file: {DECISION_PATH}")
    print()

    # Load Q-table
    qtable  = load_or_init_qtable(MODEL_PATH)
    epsilon = EPSILON_START
    tick    = 0

    # Track previous state for Q-update on next tick
    prev_state_key = None
    prev_action_map: dict[str, int] = {}   # {flow_id: action_index}
    prev_paths_map:  dict[str, list] = {}  # {flow_id: (src, dst)}

    state_json_path = os.path.join(SHARED_DIR, "state.json")

    running = {"active": True}

    def handle_shutdown(sig, frame):
        print("\nShutdown signal — saving Q-table and exiting...")
        running["active"] = False

    signal.signal(signal.SIGINT,  handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    print("Agent running — press Ctrl+C to stop")
    print("-" * 60)

    while running["active"]:
        tick_start = time.time()

        state_data = read_state(state_json_path)

        if state_data is None:
            print(f"Tick {tick:>4} | Waiting for state.json …")
        else:
            sim_tick = state_data.get("tick", "?")
            summary  = state_data.get("summary", {})
            flows    = state_data.get("flows", [])

            # ── Q-update from previous tick ────────────────────────────────
            if prev_state_key is not None and flows:
                link_metrics  = state_data.get("link_metrics", {})
                curr_state_key = build_state_key(link_metrics, link_order)
                reward         = compute_reward(summary)

                # Update Q-values for each flow's chosen action
                for flow in flows:
                    src = flow["source"]
                    dst = flow["destination"]
                    fid = flow["flow_id"]
                    paths = all_paths.get((src, dst), [])
                    if not paths:
                        continue
                    action = prev_action_map.get(fid)
                    if action is None:
                        continue
                    q_update(qtable, prev_state_key, action, reward, curr_state_key, len(paths))

            # ── Select actions for current tick ────────────────────────────
            routes, state_key = decide_routes(
                state_data, all_paths, link_order, qtable, epsilon, G
            )

            # Track which action index was chosen per flow for next tick's update
            link_metrics = state_data.get("link_metrics", {})
            curr_key     = build_state_key(link_metrics, link_order)
            prev_action_map = {}
            for flow in flows:
                src   = flow["source"]
                dst   = flow["destination"]
                fid   = flow["flow_id"]
                paths = all_paths.get((src, dst), [])
                if not paths:
                    continue
                chosen_path = routes.get(fid, [])
                # Find the index of the chosen path (may be Dijkstra fallback = index 0)
                try:
                    action_idx = paths.index(chosen_path)
                except ValueError:
                    action_idx = 0
                prev_action_map[fid] = action_idx

            prev_state_key = state_key

            # ── Write decision ─────────────────────────────────────────────
            write_decision(DECISION_PATH, routes, tick)

            # ── Decay exploration ──────────────────────────────────────────
            epsilon = max(EPSILON_MIN, epsilon * EPSILON_DECAY)

            avg_util  = summary.get("avg_utilization", 0) * 100
            congested = summary.get("congested_links", "?")
            total_lnk = summary.get("total_links",     "?")
            avg_delay = summary.get("avg_effective_delay", 0)
            reward_val = compute_reward(summary)

            print(
                f"Tick {tick:>4} | sim_tick={sim_tick:<4} | "
                f"flows={len(flows)} | util={avg_util:.1f}% avg | "
                f"delay={avg_delay:.1f}ms | congested={congested}/{total_lnk} | "
                f"reward={reward_val:+.3f} | ε={epsilon:.3f} | "
                f"states={len(qtable)}"
            )

        # Save Q-table every 20 ticks to persist online learning
        if tick > 0 and tick % 20 == 0:
            save_qtable(qtable, MODEL_PATH)
            print(f"          | Q-table checkpoint saved ({len(qtable)} states)")

        elapsed    = time.time() - tick_start
        sleep_time = max(0.0, TICK_INTERVAL - elapsed)
        time.sleep(sleep_time)
        tick += 1

    # Final save on shutdown
    save_qtable(qtable, MODEL_PATH)
    print("-" * 60)
    print(f"Agent stopped after {tick} ticks. Q-table saved.")


if __name__ == "__main__":
    main()
