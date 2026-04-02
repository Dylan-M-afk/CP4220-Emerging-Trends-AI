import sys
import json
from collections import deque
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QGraphicsScene, QGraphicsView,
    QGraphicsEllipseItem, QGraphicsLineItem, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPen, QPainter
import pyqtgraph as pg

# -----------------------------
# Config
# -----------------------------
API_FILE = Path("project/shared/state.json")
FETCH_INTERVAL = 2000  # ms
ANIM_INTERVAL = 50  # ms
STEPS_PER_FLOW = 60

NODE_RADIUS = 10
FLOW_RADIUS = 5

# -----------------------------
# Node positions (scaled)
# -----------------------------
pos = {
    "A": (50, 50), "B": (30, 10), "C": (70, 10),
    "D": (10, 55), "E": (25, 65), "F": (35, 70),
    "G": (50, 80), "H": (60, 65), "J": (70, 50),
    "K": (90, 45), "L": (70, 70),
}
SCALE = 7
pos_scaled = {n: (x*SCALE, y*SCALE) for n, (x, y) in pos.items()}

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
# Helper
# -----------------------------
def interpolate(p1, p2, steps):
    x0, y0 = p1
    x1, y1 = p2
    return [(x0 + (x1 - x0) * t / steps, y0 + (y1 - y0) * t / steps) for t in range(steps)]

# -----------------------------
# Dashboard Widget
# -----------------------------
class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Network Dashboard")
        self.resize(1000, 700)

        # --- Tabs ---
        self.tabs = QTabWidget()
        self.tab_network = QWidget()
        self.tab_line = QWidget()
        self.tab_bar = QWidget()
        self.tabs.addTab(self.tab_network, "Network Animation")
        self.tabs.addTab(self.tab_line, "Average Utilization")
        self.tabs.addTab(self.tab_bar, "Max Link Utilization per Node")

        # --- Network Tab ---
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHints(self.view.renderHints() | QPainter.RenderHint.Antialiasing)
        layout_net = QVBoxLayout()
        layout_net.addWidget(self.view)
        self.tab_network.setLayout(layout_net)

        # --- Average Utilization Tab ---
        self.graph_widget = pg.PlotWidget(title="Average Network Utilization (%)")
        self.graph_widget.setYRange(0, 100)
        self.avg_history = deque(maxlen=50)
        self.line_curve = self.graph_widget.plot(pen=pg.mkPen('b', width=2))
        layout_line = QVBoxLayout()
        layout_line.addWidget(self.graph_widget)
        self.tab_line.setLayout(layout_line)

        # --- Node Utilization Bar Graph Tab ---
        self.bar_widget = pg.PlotWidget(title="Max Link Utilization per Node (%)")
        self.bar_widget.setYRange(0, 100)
        self.node_x = list(range(len(pos)))
        self.node_names = list(pos.keys())
        ax = self.bar_widget.getAxis('bottom')
        ax.setTicks([list(zip(self.node_x, self.node_names))])
        self.bar_displayed = [0]*len(pos)
        self.bar_target = [0]*len(pos)
        layout_bar = QVBoxLayout()
        layout_bar.addWidget(self.bar_widget)
        self.tab_bar.setLayout(layout_bar)

        # --- Main Layout ---
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

        # --- Initialize network ---
        self.node_items = {}
        self.edge_items = {}
        self.flows = []

        self.node_utilization = {n: 0 for n in pos}
        self.build_topology()

        # --- Timers ---
        self.anim_timer = QTimer()
        self.anim_timer.timeout.connect(self.update_animation)
        self.anim_timer.start(ANIM_INTERVAL)

        self.fetch_timer = QTimer()
        self.fetch_timer.timeout.connect(self.fetch_state)
        self.fetch_timer.start(FETCH_INTERVAL)

    # -----------------------------
    # Build nodes & edges
    # -----------------------------
    def build_topology(self):
        for src, dst in topology_edges:
            x0, y0 = pos_scaled[src]
            x1, y1 = pos_scaled[dst]
            line = QGraphicsLineItem(x0, y0, x1, y1)
            line.setPen(QPen(QColor("green"), 3))
            self.scene.addItem(line)
            self.edge_items[f"{src}<->{dst}"] = line
        for node, (x, y) in pos_scaled.items():
            ellipse = QGraphicsEllipseItem(-NODE_RADIUS, -NODE_RADIUS, NODE_RADIUS*2, NODE_RADIUS*2)
            ellipse.setPos(x, y)
            ellipse.setBrush(QBrush(QColor("lightgray")))
            ellipse.setPen(QPen(Qt.GlobalColor.black))
            self.scene.addItem(ellipse)
            self.node_items[node] = ellipse

    # -----------------------------
    # Fetch state
    # -----------------------------
    def fetch_state(self):
        try:
            with open(API_FILE, "r") as f:
                state = json.load(f)
        except Exception as e:
            print("Error reading state:", e)
            return

        # --- Flows ---
        for route in state.get("routes", []):
            path_nodes = route.get("path", [])
            if len(path_nodes) < 2:
                continue
            xs_total = []
            for i in range(len(path_nodes)-1):
                xs_total += interpolate(pos_scaled[path_nodes[i]], pos_scaled[path_nodes[i+1]], STEPS_PER_FLOW)
            # skip duplicate flows
            if any(f['flow_id'] == route['flow_id'] for f in self.flows):
                continue
            ellipse = QGraphicsEllipseItem(-FLOW_RADIUS, -FLOW_RADIUS, FLOW_RADIUS*2, FLOW_RADIUS*2)
            ellipse.setBrush(QBrush(QColor("blue")))
            self.scene.addItem(ellipse)
            self.flows.append({
                "flow_id": route["flow_id"],
                "path": xs_total,
                "current_step": 0,
                "graphics_item": ellipse
            })

        # --- Update edge utilization & node bar targets ---
        link_metrics = state.get("link_metrics", {})
        # edge coloring & bar graph target values
        max_node_util = {n: 0 for n in pos}
        for link, metrics in link_metrics.items():
            utilization = metrics.get("utilization", 0)
            color = QColor(255,0,0) if utilization >= 0.75 else QColor(0,255,0)
            # Update edge color
            if link in self.edge_items:
                self.edge_items[link].setPen(QPen(color, 3))
            else:
                # links might be reversed
                rev = "<->".join(link.split("<->")[::-1])
                if rev in self.edge_items:
                    self.edge_items[rev].setPen(QPen(color,3))
            # update max per node
            nodes = link.split("<->")
            for n in nodes:
                max_node_util[n] = max(max_node_util[n], utilization*100)
        self.bar_target = [max_node_util[n] for n in self.node_names]

        # --- Average utilization for line chart ---
        avg_util = sum(max_node_util.values()) / len(pos)
        self.avg_history.append(avg_util)
        self.line_curve.setData(list(self.avg_history))

    # -----------------------------
    # Animate flows & update visuals
    # -----------------------------
    def update_animation(self):
        step_size = 2  # percent per frame

        # --- Animate flows ---
        active_flows = []
        for flow in self.flows:
            step = flow["current_step"]
            if step < len(flow["path"]):
                x, y = flow["path"][step]
                flow["graphics_item"].setPos(x, y)
                flow["current_step"] += 1
                active_flows.append(flow)
            else:
                self.scene.removeItem(flow["graphics_item"])
        self.flows[:] = active_flows

        # --- Step-based bar graph animation ---
        for i, target in enumerate(self.bar_target):
            if self.bar_displayed[i] < target:
                self.bar_displayed[i] = min(self.bar_displayed[i] + step_size, target)
            elif self.bar_displayed[i] > target:
                self.bar_displayed[i] = max(self.bar_displayed[i] - step_size, target)

        # Clear previous bars and redraw
        self.bar_widget.clear()
        for i, val in enumerate(self.bar_displayed):
            color = QColor(255, 0, 0) if val >= 75 else QColor(0, 255, 0)
            bar = pg.BarGraphItem(x=[self.node_x[i]], height=[val], width=0.6, brush=color)
            self.bar_widget.addItem(bar)

# -----------------------------
# Run application
# -----------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    dashboard = Dashboard()
    dashboard.show()
    sys.exit(app.exec())