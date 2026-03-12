# dashboard.py
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import requests
import math

# URL of your FastAPI endpoint
API_URL = "http://127.0.0.1:8000/state"

app = dash.Dash(__name__)
app.title = "Network Dashboard"

app.layout = html.Div([
    html.H2("Network Flow Visualization"),
    dcc.Graph(id="network-graph"),
    dcc.Interval(
        id="interval-component",
        interval=5*1000,  # 5 seconds
        n_intervals=0
    )
])

@app.callback(
    Output("network-graph", "figure"),
    Input("interval-component", "n_intervals")
)
def update_graph(n):
    try:
        response = requests.get(API_URL)
        response.raise_for_status()
        state = response.json()
    except Exception as e:
        print("API error:", e)
        return go.Figure()  # return empty figure on error

    # Extract nodes and edges from routes
    edges = []
    for route in state.get("routes", []):
        path = route.get("path", [])
        for i in range(len(path)-1):
            edges.append((path[i], path[i+1], route.get("size_mbps", 1)))

    # Unique nodes
    nodes = sorted({n for e in edges for n in e[:2]})

    # Circular layout
    N = len(nodes)
    radius = 50
    center_x, center_y = 50, 50
    pos = {}
    for idx, node in enumerate(nodes):
        angle = 2 * math.pi * idx / N
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        pos[node] = (x, y)

    # Create figure
    fig = go.Figure()

    # Draw edges
    for src, dst, size in edges:
        fig.add_trace(go.Scatter(
            x=[pos[src][0], pos[dst][0]],
            y=[pos[src][1], pos[dst][1]],
            mode="lines",
            line=dict(width=max(size/20, 1), color="blue"),
            hoverinfo="text",
            text=f"{src} -> {dst}: {size} Mbps"
        ))

    # Draw nodes
    for node in nodes:
        fig.add_trace(go.Scatter(
            x=[pos[node][0]],
            y=[pos[node][1]],
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

    return fig

if __name__ == "__main__":
    app.run(debug=True)