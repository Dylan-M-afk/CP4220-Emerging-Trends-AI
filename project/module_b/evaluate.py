# ========== Evaluation ==========
# Compares RL agent performance against the Dijkstra baseline using history.csv
# and shows what routing decisions the RL agent is currently making via state.json.
#
# Run at any time while the simulation is live, or after it finishes:
#   python evaluate.py

import os
import json
import csv
import pickle
import numpy as np
import networkx as nx
import yaml
from datetime import datetime

# ─── Paths ────────────────────────────────────────────────────────────────────

MODULE_DIR    = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR    = os.path.abspath(os.path.join(MODULE_DIR, "../shared"))
TOPOLOGY_PATH = os.path.abspath(os.path.join(MODULE_DIR, "../module_a/topology.yaml"))
HISTORY_PATH  = os.path.join(SHARED_DIR, "history.csv")
STATE_PATH    = os.path.join(SHARED_DIR, "state.json")
DECISION_PATH = os.path.join(SHARED_DIR, "routing_decision.json")
MODEL_PATH    = os.path.join(MODULE_DIR,  "qtable.pkl")

CONGESTION_THRESH = 0.75

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_graph():
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

def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, newline="") as f:
        return list(csv.DictReader(f))

def path_latency(G, path):
    return sum(G[path[i]][path[i+1]]["latency_ms"] for i in range(len(path) - 1))

def path_hops(path):
    return len(path) - 1

def sep(char="─", width=62):
    print(char * width)

# ─── Section 1: History comparison ────────────────────────────────────────────

def compare_strategies(rows):
    sep("═")
    print("  SECTION 1 — Strategy Comparison (history.csv)")
    sep("═")

    dijkstra_rows = [r for r in rows if r["strategy"] == "dijkstra"]
    rl_rows       = [r for r in rows if r["strategy"] == "rl"]

    if not dijkstra_rows:
        print("  No dijkstra rows found in history.")
        return
    if not rl_rows:
        print(f"  {len(dijkstra_rows)} dijkstra rows found.")
        print("  No RL rows yet — keep the agent running and re-run evaluate.py.")
        print()
        # Still print dijkstra stats as baseline reference
        print_strategy_stats("Dijkstra baseline", dijkstra_rows)
        return

    print(f"  Dijkstra rows : {len(dijkstra_rows)}")
    print(f"  RL rows       : {len(rl_rows)}")
    print()

    def stats(rows, col):
        vals = [float(r[col]) for r in rows]
        return np.mean(vals), np.min(vals), np.max(vals)

    metrics = [
        ("avg_utilization",      "Avg utilization",       True,  "{:.1f}%",  100),
        ("max_utilization",      "Peak utilization",      True,  "{:.1f}%",  100),
        ("avg_effective_delay",  "Avg delay (ms)",        True,  "{:.2f}ms", 1),
        ("max_effective_delay",  "Peak delay (ms)",       True,  "{:.2f}ms", 1),
        ("avg_effective_loss",   "Avg loss rate",         True,  "{:.5f}",   1),
        ("congested_links",      "Congested links (avg)", True,  "{:.2f}",   1),
    ]

    print(f"  {'Metric':<28} {'Dijkstra':>12} {'RL':>12} {'Δ':>10} {'Better?':>8}")
    sep()

    for col, label, lower_is_better, fmt, scale in metrics:
        d_mean, _, _ = stats(dijkstra_rows, col)
        r_mean, _, _ = stats(rl_rows, col)

        d_str = fmt.format(d_mean * scale)
        r_str = fmt.format(r_mean * scale)

        delta     = r_mean - d_mean
        delta_pct = (delta / d_mean * 100) if d_mean != 0 else 0

        if lower_is_better:
            better = "RL ✓" if r_mean < d_mean else ("TIE" if r_mean == d_mean else "BASE ✓")
        else:
            better = "RL ✓" if r_mean > d_mean else ("TIE" if r_mean == d_mean else "BASE ✓")

        delta_str = f"{delta_pct:+.1f}%"
        print(f"  {label:<28} {d_str:>12} {r_str:>12} {delta_str:>10} {better:>8}")

    sep()

    # Congestion event counts
    d_congested = sum(1 for r in dijkstra_rows if int(r["congested_links"]) > 0)
    r_congested = sum(1 for r in rl_rows       if int(r["congested_links"]) > 0)
    d_pct = d_congested / len(dijkstra_rows) * 100
    r_pct = r_congested / len(rl_rows)       * 100

    print(f"\n  Ticks with any congestion:")
    print(f"    Dijkstra : {d_congested}/{len(dijkstra_rows)} ticks  ({d_pct:.1f}%)")
    print(f"    RL       : {r_congested}/{len(rl_rows)} ticks  ({r_pct:.1f}%)")
    if r_pct < d_pct:
        print(f"    → RL reduced congestion events by {d_pct - r_pct:.1f} percentage points")
    elif r_pct > d_pct:
        print(f"    → RL had more congestion events by {r_pct - d_pct:.1f} percentage points")
    else:
        print(f"    → No difference in congestion frequency")

    print()

def print_strategy_stats(label, rows):
    """Print stats for a single strategy — used when only one strategy has data."""
    def mean(col):
        return np.mean([float(r[col]) for r in rows])
    print(f"  {label} ({len(rows)} ticks):")
    print(f"    Avg utilization : {mean('avg_utilization')*100:.1f}%")
    print(f"    Avg delay       : {mean('avg_effective_delay'):.2f} ms")
    print(f"    Avg loss        : {mean('avg_effective_loss'):.5f}")
    congested = sum(1 for r in rows if int(r["congested_links"]) > 0)
    print(f"    Congested ticks : {congested}/{len(rows)}")
    print()

# ─── Section 2: Current RL decisions ──────────────────────────────────────────

def show_current_decisions(G):
    sep("═")
    print("  SECTION 2 — Current RL Routing Decisions")
    sep("═")

    if not os.path.exists(STATE_PATH):
        print("  state.json not found — is the simulator running?")
        return
    if not os.path.exists(DECISION_PATH):
        print("  routing_decision.json not found — is the RL agent running?")
        return

    with open(STATE_PATH) as f:
        state = json.load(f)
    with open(DECISION_PATH) as f:
        decision = json.load(f)

    flows        = {f["flow_id"]: f for f in state.get("flows", [])}
    rl_routes    = decision.get("routes", {})
    sim_tick     = state.get("tick", "?")
    agent_tick   = decision.get("tick", "?")

    print(f"  Simulator tick  : {sim_tick}")
    print(f"  Agent tick      : {agent_tick}")
    print()

    # Compute Dijkstra paths for comparison
    dijkstra_paths = {}
    for fid, flow in flows.items():
        try:
            dijkstra_paths[fid] = nx.shortest_path(
                G, flow["source"], flow["destination"], weight="latency_ms"
            )
        except nx.NetworkXNoPath:
            dijkstra_paths[fid] = []

    print(f"  {'Flow':<22} {'Src→Dst':<8} {'RL Path':<30} {'Dijkstra Path':<30} {'Same?'}")
    sep()

    for fid, flow in flows.items():
        src      = flow["source"]
        dst      = flow["destination"]
        rl_path  = rl_routes.get(fid, [])
        dj_path  = dijkstra_paths.get(fid, [])

        rl_str = " → ".join(rl_path)  if rl_path  else "none"
        dj_str = " → ".join(dj_path)  if dj_path  else "none"
        same   = "yes" if rl_path == dj_path else "NO — different"

        print(f"  {fid:<22} {src}→{dst:<5}  {rl_str:<30} {dj_str:<30} {same}")

    # Count divergences
    diverged = sum(1 for fid in flows if rl_routes.get(fid) != dijkstra_paths.get(fid))
    print()
    print(f"  RL chose a different path for {diverged}/{len(flows)} flows this tick")

    # Latency comparison for diverged flows
    if diverged > 0:
        print()
        print(f"  Latency breakdown for diverged flows:")
        print(f"  {'Flow':<22} {'RL latency':>12} {'Dijkstra latency':>18} {'Δ':>8}")
        sep()
        for fid, flow in flows.items():
            rl_path = rl_routes.get(fid, [])
            dj_path = dijkstra_paths.get(fid, [])
            if rl_path == dj_path or not rl_path or not dj_path:
                continue
            try:
                rl_lat = path_latency(G, rl_path)
                dj_lat = path_latency(G, dj_path)
                delta  = rl_lat - dj_lat
                note   = "longer path" if delta > 0 else "shorter path"
                print(f"  {fid:<22} {rl_lat:>10.0f}ms {dj_lat:>16.0f}ms {delta:>+7.0f}ms  ({note})")
            except (KeyError, nx.NetworkXError):
                continue

        print()
        print("  Note: a longer individual path can still improve overall network")
        print("  performance by redistributing load away from congested links.")

    print()

# ─── Section 3: Q-table insight ───────────────────────────────────────────────

def show_qtable_info():
    sep("═")
    print("  SECTION 3 — Q-table Summary")
    sep("═")

    if not os.path.exists(MODEL_PATH):
        print("  qtable.pkl not found — run train.py first.")
        print()
        return

    with open(MODEL_PATH, "rb") as f:
        qtable = pickle.load(f)

    print(f"  States explored : {len(qtable)}")

    if not qtable:
        print("  Q-table is empty.")
        print()
        return

    # Find the states where the agent most strongly prefers a non-baseline action
    divergences = []
    for state, q_vals in qtable.items():
        if len(q_vals) < 2:
            continue
        best_action = int(np.argmax(q_vals))
        if best_action != 0:  # 0 = Dijkstra baseline
            confidence = q_vals[best_action] - q_vals[0]
            divergences.append((state, best_action, q_vals[best_action], q_vals[0], confidence))

    divergences.sort(key=lambda x: x[4], reverse=True)

    pct = len(divergences) / len(qtable) * 100 if qtable else 0
    print(f"  States preferring non-baseline path : {len(divergences)}/{len(qtable)}  ({pct:.1f}%)")

    if divergences:
        print()
        print(f"  Top 5 states where RL most confidently diverges from Dijkstra:")
        print(f"  {'State (util bins)':<30} {'Best action':>12} {'RL Q':>10} {'Base Q':>10} {'Δ Q':>8}")
        sep()
        for state, action, rl_q, base_q, conf in divergences[:5]:
            print(f"  {str(state):<30} {action:>12} {rl_q:>10.4f} {base_q:>10.4f} {conf:>+8.4f}")

    print()

# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    print()
    sep("═")
    print("  Module B — RL Evaluation Report")
    print(f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    sep("═")
    print()

    G    = load_graph()
    rows = load_history()

    if not rows:
        print("No history.csv found. Run the simulator first.")
        return

    print(f"  history.csv: {len(rows)} total ticks\n")

    compare_strategies(rows)
    show_current_decisions(G)
    show_qtable_info()

    sep("═")
    print("  End of report")
    sep("═")
    print()

if __name__ == "__main__":
    main()