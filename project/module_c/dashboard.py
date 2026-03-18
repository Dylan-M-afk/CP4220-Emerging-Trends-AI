# dashboard.py
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import requests
import math

API_URL = "http://127.0.0.1:8000/state"

app = dash.Dash(__name__)
app.title = "Network Dashboard"

# --- Static node positions (update manually if you want) ---
pos = {
    "A": (50, 50),
    "B": (30, 10),
    "C": (70, 10),
    "D": (10, 55),
    "E": (25, 65),
    "F": (35, 70),
    "G": (50, 80),
    "H": (60, 65),
    "J": (70, 50),
    "K": (90, 45),
    "L": (70, 70),
}

# --- Animation parameters ---
EDGE_ANIM_STEPS = 200  # number of steps to animate each edge
UPDATE_INTERVAL = 20  # ms per animation step (so 20 steps = 5 sec)

# --- Global variables to track animation ---
edges_to_animate = []
current_step = 0

app.layout = html.Div([
    html.H2("Network Flow Visualization"),
    dcc.Graph(id="network-graph"),
    dcc.Interval(
        id="interval-component",
        interval=UPDATE_INTERVAL,
        n_intervals=0
    )
])

# --- Helper function to interpolate edge positions ---
def interpolate_edge(src, dst, steps=EDGE_ANIM_STEPS):
    x0, y0 = pos[src]
    x1, y1 = pos[dst]
    xs = [x0 + (x1 - x0) * t/steps for t in range(1, steps+1)]
    ys = [y0 + (y1 - y0) * t/steps for t in range(1, steps+1)]
    return xs, ys

@app.callback(
    Output("network-graph", "figure"),
    Input("interval-component", "n_intervals")
)
def update_graph(n):
    global edges_to_animate, current_step

    # Fetch latest state from API only when starting a new animation
    if current_step == 0:
        try:
            response = requests.get(API_URL)
            response.raise_for_status()
            state = response.json()
        except Exception as e:
            print("API error:", e)
            return go.Figure()

        # Extract edges
        edges_to_animate = []
        for route in state.get("routes", []):
            path = route.get("path", [])
            for i in range(len(path)-1):
                src, dst = path[i], path[i+1]
                size = route.get("size_mbps", 1)
                xs, ys = interpolate_edge(src, dst, EDGE_ANIM_STEPS)
                edges_to_animate.append({
                    "src": src, "dst": dst, "size": size, "xs": xs, "ys": ys
                })

    # Build figure
    fig = go.Figure()

    # Draw edges up to current animation step
    for edge in edges_to_animate:
        fig.add_trace(go.Scatter(
            x=edge["xs"][:current_step+1],
            y=edge["ys"][:current_step+1],
            mode="lines",
            line=dict(width=max(edge["size"]/20, 1), color="blue"),
            hoverinfo="text",
            text=f"{edge['src']} -> {edge['dst']}: {edge['size']} Mbps"
        ))

    # Draw all nodes
    for node, (x, y) in pos.items():
        fig.add_trace(go.Scatter(
            x=[x], y=[y],
            mode="markers+text",
            marker=dict(size=20, color="red"),
            text=[node],
            textposition="bottom center"
        ))

    fig.update_layout(
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor="white",
        margin=dict(l=20, r=20, t=20, b=20)
    )

    # Increment animation step
    current_step += 1
    if current_step >= EDGE_ANIM_STEPS:
        current_step = 0  # reset for next API fetch

    return fig

if __name__ == "__main__":
    app.run(debug=True)