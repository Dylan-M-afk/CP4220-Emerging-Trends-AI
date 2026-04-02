import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import requests

# -----------------------------
# API
# -----------------------------
API_URL = "http://127.0.0.1:8000/state"

# -----------------------------
# Node positions
# -----------------------------
pos = {
    "A": (50, 50), "B": (30, 10), "C": (70, 10),
    "D": (10, 55), "E": (25, 65), "F": (35, 70),
    "G": (50, 80), "H": (60, 65), "J": (70, 50),
    "K": (90, 45), "L": (70, 70),
}

# -----------------------------
# Topology edges
# -----------------------------
topology_edges = [
    ("A", "B"), ("A", "F"), ("A", "G"), ("A", "H"),
    ("B", "C"), ("B", "D"), ("C", "J"),
    ("D", "E"), ("E", "F"), ("F", "G"),
    ("G", "L"), ("H", "J"), ("J", "K"), ("K", "L")
]

# -----------------------------
# Config
# -----------------------------
STEPS = 90
ANIM_INTERVAL = 50
FETCH_INTERVAL = 3000
BAR_ANIM_STEPS = 15
MAX_HISTORY = 100  # performance line points

# -----------------------------
# Global state
# -----------------------------
flow_cache = []
node_utilization = {}
prev_node_utilization = {}
node_colors = {}
bar_step_index = 0
bar_animating = False
performance_history = []

# -----------------------------
# Interpolation
# -----------------------------
def interpolate(src, dst, steps):
    x0, y0 = pos[src]
    x1, y1 = pos[dst]
    xs = [x0 + (x1 - x0) * t / steps for t in range(steps)]
    ys = [y0 + (y1 - y0) * t / steps for t in range(steps)]
    return xs, ys

# -----------------------------
# Build static topology edges
# -----------------------------
def build_static_edges():
    traces = []
    for src, dst in topology_edges:
        xs, ys = interpolate(src, dst, 2)
        traces.append(go.Scattergl(
            x=[xs[0], xs[-1]],
            y=[ys[0], ys[-1]],
            mode="lines",
            line=dict(width=1, color="lightgray"),
            hoverinfo="none"
        ))
    return traces

edges_static = build_static_edges()

# -----------------------------
# Build node traces
# -----------------------------
def build_nodes():
    traces = []
    for node, (x, y) in pos.items():
        traces.append(go.Scattergl(
            x=[x], y=[y],
            mode="markers+text",
            marker=dict(size=18, color=node_colors.get(node, "green")),
            text=[node],
            textposition="bottom center",
            hoverinfo="none"
        ))
    return traces

# -----------------------------
# Dash app layout
# -----------------------------
app = dash.Dash(__name__)
app.title = "Network Dashboard"

app.layout = html.Div([
    html.H2("Network Dashboard"),
    dcc.Tabs([
        dcc.Tab(label="Network Animation", children=[
            dcc.Graph(id="network-graph")
        ]),
        dcc.Tab(label="Node Congestion", children=[
            dcc.Graph(id="node-congestion")
        ]),
        dcc.Tab(label="Performance Over Time", children=[
            dcc.Graph(id="performance-line")
        ])
    ]),
    dcc.Interval(id="fetch-loop", interval=FETCH_INTERVAL, n_intervals=0),
    dcc.Interval(id="anim-loop", interval=ANIM_INTERVAL, n_intervals=0)
])

# -----------------------------
# Fetch loop
# -----------------------------
@app.callback(
    Output("network-graph", "figure", allow_duplicate=True),
    Input("fetch-loop", "n_intervals"),
    prevent_initial_call=True
)
def fetch_data(_):
    global flow_cache, node_utilization, prev_node_utilization, node_colors
    global bar_step_index, bar_animating, performance_history

    try:
        res = requests.get(API_URL)
        res.raise_for_status()
        state = res.json()
    except Exception as e:
        print("API error:", e)
        return dash.no_update

    # --- Node utilization ---
    prev_node_utilization = node_utilization.copy()
    node_utilization = {}
    node_colors = {}
    for node in pos:
        max_util = 0
        for edge_key, metrics in state.get("link_metrics", {}).items():
            if node in edge_key:
                max_util = max(max_util, metrics["utilization"])
        node_utilization[node] = max_util
        node_colors[node] = "red" if max_util >= 0.75 else "green"

    # Reset bar animation
    bar_step_index = 0
    bar_animating = True

    # --- Add new flows ---
    for route in state.get("routes", []):
        path = route.get("path", [])
        if len(path) < 2:
            continue
        xs_total, ys_total = [], []
        for i in range(len(path)-1):
            xs, ys = interpolate(path[i], path[i+1], STEPS)
            xs_total += xs
            ys_total += ys
        if any(f['flow_id'] == route['flow_id'] for f in flow_cache):
            continue
        flow_cache.append({
            "flow_id": route["flow_id"],
            "xs": xs_total,
            "ys": ys_total,
            "size_mbps": route.get("size_mbps", 10),
            "current_step": 0,
            "steps": len(xs_total)
        })

    # --- Update performance history ---
    avg_util = sum(node_utilization.values()) / len(node_utilization)
    performance_history.append(avg_util)
    if len(performance_history) > MAX_HISTORY:
        performance_history.pop(0)

    return dash.no_update

# -----------------------------
# Animation loop
# -----------------------------
@app.callback(
    Output("network-graph", "figure"),
    Output("node-congestion", "figure"),
    Output("performance-line", "figure"),
    Input("anim-loop", "n_intervals")
)
def animate(_):
    global flow_cache, bar_step_index, bar_animating

    # --- Network figure ---
    fig_net = go.Figure()
    fig_net.add_traces(edges_static)
    fig_net.add_traces(build_nodes())

    active_flows = []
    for flow in flow_cache:
        step = flow["current_step"]
        if step < flow["steps"]:
            fig_net.add_trace(go.Scattergl(
                x=[flow["xs"][step]],
                y=[flow["ys"][step]],
                mode="markers",
                marker=dict(
                    size=max(flow["size_mbps"]/10, 4),
                    color="blue"
                ),
                hoverinfo="text",
                text=f"{flow['flow_id']}: {flow['size_mbps']:.1f} Mbps"
            ))
            flow["current_step"] += 1
            active_flows.append(flow)
    flow_cache[:] = active_flows

    # --- Node congestion bars ---
    if bar_animating:
        t = (bar_step_index + 1) / BAR_ANIM_STEPS
        y_vals = []
        for node in pos:
            prev = prev_node_utilization.get(node, 0)
            curr = node_utilization.get(node, 0)
            y_vals.append(prev + (curr - prev) * t)
        bar_step_index += 1
        if bar_step_index >= BAR_ANIM_STEPS:
            bar_step_index = 0
            bar_animating = False
    else:
        y_vals = [node_utilization.get(node, 0) for node in pos]

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        x=list(pos.keys()),
        y=[v*100 for v in y_vals],
        marker_color=[node_colors[n] for n in pos],
        text=[f"{v*100:.1f}%" for v in y_vals],
        textposition="auto"
    ))
    fig_bar.update_layout(
        yaxis=dict(title="Utilization (%)", range=[0,100]),
        xaxis=dict(title="Node"),
        margin=dict(l=20,r=20,t=20,b=20),
        showlegend=False
    )

    # --- Performance line ---
    fig_perf = go.Figure()
    fig_perf.add_trace(go.Scatter(
        y=[v*100 for v in performance_history],
        mode="lines+markers",
        line=dict(color="orange", width=2),
        marker=dict(size=4)
    ))
    fig_perf.update_layout(
        yaxis=dict(title="Avg Node Utilization (%)", range=[0,100]),
        xaxis=dict(title="Time (ticks)"),
        margin=dict(l=20,r=20,t=20,b=20)
    )

    # --- Network layout tweaks ---
    fig_net.update_layout(
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, visible=False),
        yaxis=dict(showgrid=False, zeroline=False, visible=False),
        margin=dict(l=20,r=20,t=20,b=20),
        uirevision="constant",
        transition_duration=0
    )

    return fig_net, fig_bar, fig_perf

# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)