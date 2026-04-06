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
STATE_PATH    = os.path.join(SHARED_DIR, "state.json")
DECISION_PATH = os.path.join(SHARED_DIR, "routing_decision.json")
MODEL_PATH    = os.path.join(MODULE_DIR,  "qtable.pkl")

# ─── Hyper-parameters ─────────────────────────────────────────────────────────

TICK_INTERVAL     = float(os.environ.get("RL_TICK_INTERVAL", 5))  # synced from run.py, or default 5s
ALPHA             = 0.1    # online learning rate (lower than offline to avoid oscillation)
GAMMA             = 0.9    # discount factor
EPSILON_START     = 0.3
EPSILON_MIN       = 0.02   # floor — always keep a tiny chance of exploration
EPSILON_DECAY     = 0.999
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

def build_global_state_key(link_metrics: dict, link_order: list) -> tuple:
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

def build_flow_state_key(src: str, dst: str, paths: list, link_metrics_map: dict) -> tuple:
    """
    Build a state key using the utilization of every link that appears in any
    candidate path for this (src, dst) pair.

    The links are sorted for stability so the same set always produces the
    same tuple layout regardless of path ordering.

    Args:
        src / dst         : flow endpoints
        paths             : candidate path list from all_paths[(src, dst)]
        link_metrics_map  : {(min_node, max_node): utilization float}
                            pre-built once per tick from state.json

    Returns:
        A tuple of int bins, one per unique candidate link — hashable state key.
    """
    # Collect every unique link that appears across all candidate paths
    candidate_links: set[tuple[str, str]] = set()
    for path in paths:
        for i in range(len(path) - 1):
            candidate_links.add((min(path[i], path[i+1]), max(path[i], path[i+1])))

    # Sort for a stable tuple layout
    for link in sorted(candidate_links):
        pass 

    return tuple(
        discretise_utilization(link_metrics_map.get(lk, 0.0))
        for lk in sorted(candidate_links)
    )

def build_link_metrics_map(link_metrics: dict) -> dict:
    """
    Convert state.json link_metrics (keyed "A<->B") to a dict keyed by
    (min_node, max_node) tuples for fast lookup inside build_flow_state_key.
    Built once per tick and reused for all flows.
    """
    result = {}
    for edge_str, metrics in link_metrics.items():
        parts = edge_str.split("<->")
        if len(parts) == 2:
            a, b = parts
            result[(min(a, b), max(a, b))] = metrics.get("utilization", 0.0)
    return result

# ─── Reward ───────────────────────────────────────────────────────────────────

# The per-flow reward penalises:
#   1. The average utilization of links on the chosen path  (load balancing)
#   2. The average effective delay of links on the chosen path (latency)
#   3. Any link on the path that is congested               (congestion penalty)
#
# Returns a float in roughly [-10, 0]; higher is better.
def compute_flow_reward(path: list, link_metrics: dict) -> float:
    """
    Compute a reward for one specific routing decision.

    Args:
        path         : list of node IDs chosen for this flow, e.g. ["A", "F", "E"]
        link_metrics : the raw link_metrics dict from state.json (keyed "A<->B")

    Returns:
        float reward, higher is better
    """
    if len(path) < 2:
        return -10.0  # penalise missing / empty paths heavily

    # Build a fast lookup from the state.json format
    lm_map = {}
    for edge_str, metrics in link_metrics.items():
        parts = edge_str.split("<->")
        if len(parts) == 2:
            a, b = parts
            lm_map[(min(a, b), max(a, b))] = metrics

    path_delays      = []
    path_utils       = []
    congested_hops   = 0

    for i in range(len(path) - 1):
        key = (min(path[i], path[i+1]), max(path[i], path[i+1]))
        m   = lm_map.get(key)
        if m is None:
            continue
        path_delays.append(m.get("effective_delay_ms", 0.0))
        util = m.get("utilization", 0.0)
        path_utils.append(util)
        if util > CONGESTION_THRESH:
            congested_hops += 1

    if not path_delays:
        return -10.0

    avg_delay        = sum(path_delays) / len(path_delays)
    avg_util         = sum(path_utils)  / len(path_utils)
    congestion_frac  = congested_hops   / len(path_delays)

    delay_penalty    = avg_delay / 100.0
    util_penalty     = avg_util  * 1.0
    congestion_extra = congestion_frac * 2.0

    return -(delay_penalty + util_penalty + congestion_extra)

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
    state_data    : dict,
    all_paths     : dict,
    link_order    : list,
    qtable        : dict,
    epsilon       : float,
    G             : nx.Graph,
) -> tuple[dict, dict, dict]:
    """
    Given the current state.json payload, select a path for every active flow.

    Returns:
        routes         — {flow_id: [node, …]}  ready to write to routing_decision.json
        flow_states    — {flow_id: state_key tuple}  per-flow state used this tick
        flow_actions   — {flow_id: action_index int}  action chosen per flow
    """
    link_metrics     = state_data.get("link_metrics", {})
    flows            = state_data.get("flows", [])
    # Build the shared utilization lookup once — reused by every flow
    lm_map           = build_link_metrics_map(link_metrics)

    routes       = {}
    flow_states  = {}
    flow_actions = {}

    for flow in flows:
        src = flow["source"]
        dst = flow["destination"]
        fid = flow["flow_id"]

        paths = all_paths.get((src, dst), [])

        if not paths:
            # No pre-computed path — fall back to Dijkstra
            try:
                path = nx.shortest_path(G, src, dst, weight="latency_ms")
            except nx.NetworkXNoPath:
                path = []
            routes[fid]      = path
            flow_states[fid] = ()   # empty key — no Q-update for this flow
            flow_actions[fid] = 0
            continue

        state_key   = build_flow_state_key(src, dst, paths, lm_map)
        num_actions = len(paths)
        action      = select_action(qtable, state_key, num_actions, epsilon)

        routes[fid]       = paths[action]
        flow_states[fid]  = state_key
        flow_actions[fid] = action

    return routes, flow_states, flow_actions

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
    print(f"State file: {STATE_PATH}")
    print(f"Decision file: {DECISION_PATH}")
    print()

    # Load Q-table
    qtable  = load_or_init_qtable(MODEL_PATH)
    epsilon = EPSILON_START
    tick    = 0

    prev_flow_map: dict[tuple, dict] = {}

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

        state_data = read_state(STATE_PATH)

        if state_data is None:
            print(f"Tick {tick:>4} | Waiting for state.json …")
        else:
            sim_tick     = state_data.get("tick", "?")
            summary      = state_data.get("summary", {})
            flows        = state_data.get("flows", [])
            link_metrics = state_data.get("link_metrics", {})

            # ── Q-update from previous tick ────────────────────────────────
            if prev_flow_map and flows:
                lm_map_curr = build_link_metrics_map(link_metrics)

                q_updates = 0
                # Wrap the entire update block so a bad entry never crashes the
                # agent and causes routing_decision.json to go stale.
                try:
                    for flow in flows:
                        src = flow["source"]
                        dst = flow["destination"]
                        key = (src, dst)

                        prev = prev_flow_map.get(key)
                        if prev is None:
                            continue  # this (src,dst) pair wasn't seen last tick

                        prev_state  = prev["state"]
                        prev_action = prev["action"]
                        prev_path   = prev["path"]
                        # Use the num_actions that was in effect when the action
                        # was chosen, NOT len(paths) from the current tick.
                        # This prevents an IndexError when multiple flows share
                        # the same (src, dst) key and the stored action index
                        # exceeds the current path list length.
                        num_actions = prev["num_actions"]

                        if not prev_state:
                            continue  # was a Dijkstra fallback - no Q-entry to update

                        # Sanity-check: skip if stored action is somehow out of range
                        if prev_action >= num_actions:
                            continue

                        paths = all_paths.get((src, dst), [])
                        if not paths:
                            continue

                        # Per-flow reward based on the path actually traversed
                        reward = compute_flow_reward(prev_path, link_metrics)

                        # Next-state is the current per-flow state for this pair
                        next_state = build_flow_state_key(src, dst, paths, lm_map_curr)

                        q_update(qtable, prev_state, prev_action, reward, next_state, num_actions)
                        q_updates += 1
                except Exception as e:
                    print(f"          | WARNING: Q-update error skipped — {e}")

            # ── Select actions for current tick ────────────────────────────
            routes, flow_states, flow_actions = decide_routes(
                state_data, all_paths, link_order, qtable, epsilon, G
            )

            prev_flow_map = {}
            for flow in flows:
                src = flow["source"]
                dst = flow["destination"]
                fid = flow["flow_id"]
                paths = all_paths.get((src, dst), [])
                prev_flow_map[(src, dst)] = {
                    "state"      : flow_states.get(fid, ()),
                    "action"     : flow_actions.get(fid, 0),
                    "path"       : routes.get(fid, []),
                    "num_actions": len(paths) if paths else 1,
                }

            # ── Write decision ─────────────────────────────────────────────
            write_decision(DECISION_PATH, routes, tick)

            # ── Decay exploration ──────────────────────────────────────────
            epsilon = max(EPSILON_MIN, epsilon * EPSILON_DECAY)

            avg_util  = summary.get("avg_utilization", 0) * 100
            congested = summary.get("congested_links", "?")
            total_lnk = summary.get("total_links",     "?")
            avg_delay = summary.get("avg_effective_delay", 0)
            # Display a representative per-flow reward (first flow with a path)
            sample_reward = None
            for flow in flows:
                p = routes.get(flow["flow_id"], [])
                if len(p) >= 2:
                    sample_reward = compute_flow_reward(p, link_metrics)
                    break
            reward_str = f"{sample_reward:+.3f}" if sample_reward is not None else "  n/a"

            print(
                f"Tick {tick:>4} | sim_tick={sim_tick:<4} | "
                f"flows={len(flows)} | util={avg_util:.1f}% avg | "
                f"delay={avg_delay:.1f}ms | congested={congested}/{total_lnk} | "
                f"sample_reward={reward_str} | ε={epsilon:.4f} | "
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
