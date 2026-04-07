import sys
import json
from collections import deque
from pathlib import Path
import os
import yaml

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsScene, QGraphicsView,
    QGraphicsEllipseItem, QGraphicsLineItem,
    QTabWidget, QSlider, QLabel, QCheckBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPen, QPainter
import pyqtgraph as pg

# -----------------------------
# Paths
# -----------------------------
MODULE_DIR = Path(os.path.abspath(__file__)).parent
API_FILE   = MODULE_DIR.parent / "shared" / "state.json"
CONFIG_PATH = (MODULE_DIR.parent / "module_a" / "network_parameters.yaml").resolve()

# -----------------------------
# Config
# -----------------------------
FETCH_INTERVAL = 3000
ANIM_INTERVAL = 50
STEPS_PER_FLOW = 20

NODE_RADIUS = 10
FLOW_RADIUS = 5
EDGE_UTIL_THRESHOLD = 0.75

# -----------------------------
# Node positions
# -----------------------------
pos = {
    "A": (50, 50), "B": (30, 70), "C": (70, 70),
    "D": (10, 35), "E": (25, 25), "F": (35, 20),
    "G": (50, 10), "H": (60, 25), "J": (70, 50),
    "K": (90, 55), "L": (70, 20),
}
SCALE = 7
pos_scaled = {n: (x*SCALE, y*SCALE) for n, (x, y) in pos.items()}

topology_edges = [
    ("A","B"),("A","F"),("A","G"),("A","H"),
    ("B","C"),("B","D"),("C","J"),
    ("D","E"),("E","F"),("F","G"),
    ("G","L"),("H","J"),("J","K"),("K","L")
]

# -----------------------------
# Helper
# -----------------------------
def interpolate(p1, p2, steps):
    x0,y0 = p1
    x1,y1 = p2
    return [(x0+(x1-x0)*t/steps, y0+(y1-y0)*t/steps) for t in range(steps)]

def write_config(num_transfers, use_rl):
    data = {
        "traffic": {"num_transfers": num_transfers, "min_transfer_mbps": 10, "max_transfer_mbps": 150, "seed": 42},
        "simulation": {"tick_interval_seconds": 3.0, "max_ticks": None, "shared": "../shared", "use_rl": use_rl}
    }
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(data, f)

# -----------------------------
# Dashboard
# -----------------------------
class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Network Dashboard")
        self.resize(1100, 700)

        # Tabs
        self.tabs = QTabWidget()
        self.tab_network = QWidget()
        self.tab_line = QWidget()
        self.tab_bar = QWidget()

        self.tabs.addTab(self.tab_network, "Network")
        self.tabs.addTab(self.tab_line, "Avg Utilization")
        self.tabs.addTab(self.tab_bar, "Node Utilization")

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

        # -----------------------------
        # Network Tab Layout
        # -----------------------------
        layout_net = QHBoxLayout()
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing)
        layout_net.addWidget(self.view, stretch=3)

        # --- Control Panel ---
        control = QVBoxLayout()
        # Slider
        self.slider_label = QLabel("Transfers: 16")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(5)
        self.slider.setMaximum(32)
        self.slider.setValue(16)
        self.slider.valueChanged.connect(self.on_slider_change)
        control.addWidget(self.slider_label)
        control.addWidget(self.slider)
        # Checkbox
        self.rl_checkbox = QCheckBox("Use RL")
        self.rl_checkbox.setChecked(True)
        self.rl_checkbox.stateChanged.connect(self.on_rl_change)
        control.addWidget(self.rl_checkbox)
        layout_net.addLayout(control, stretch=1)
        self.tab_network.setLayout(layout_net)

        # -----------------------------
        # Average Utilization Tab
        # -----------------------------
        self.graph_widget = pg.PlotWidget(title="Average Network Utilization (%)")
        self.graph_widget.setYRange(0, 100)
        self.avg_history = deque(maxlen=50)
        self.line_curve = self.graph_widget.plot(pen=pg.mkPen('b', width=2))
        layout_line = QVBoxLayout()
        layout_line.addWidget(self.graph_widget)
        self.tab_line.setLayout(layout_line)

        # -----------------------------
        # Node Utilization Bar Tab
        # -----------------------------
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

        # -----------------------------
        # Initialize network
        # -----------------------------
        self.node_items = {}
        self.edge_items = {}
        self.flows = []
        self.link_utilization = {}
        self.node_utilization = {n:0 for n in pos}
        self.build_topology()

        # Timers
        self.anim_timer = QTimer()
        self.anim_timer.timeout.connect(self.update_animation)
        self.anim_timer.start(ANIM_INTERVAL)

        self.fetch_timer = QTimer()
        self.fetch_timer.timeout.connect(self.fetch_state)
        self.fetch_timer.start(FETCH_INTERVAL)

        # Write initial config
        write_config(self.slider.value(), self.rl_checkbox.isChecked())

    # -----------------------------
    def build_topology(self):
        for src,dst in topology_edges:
            x0,y0 = pos_scaled[src]
            x1,y1 = pos_scaled[dst]
            line = QGraphicsLineItem(x0, y0, x1, y1)
            line.setPen(QPen(QColor("green"),3))
            self.scene.addItem(line)
            self.edge_items[f"{src}<->{dst}"] = line
        for node,(x,y) in pos_scaled.items():
            ellipse = QGraphicsEllipseItem(-NODE_RADIUS,-NODE_RADIUS,NODE_RADIUS*2,NODE_RADIUS*2)
            ellipse.setPos(x,y)
            ellipse.setBrush(QBrush(QColor("black")))
            ellipse.setPen(QPen(Qt.GlobalColor.black))
            self.scene.addItem(ellipse)
            self.node_items[node] = ellipse

    # -----------------------------
    def fetch_state(self):
        try:
            with open(API_FILE,"r") as f:
                state = json.load(f)
        except Exception as e:
            print("Error reading state:",e)
            return

        self.link_utilization = state.get("link_metrics",{})

        # Edge colors
        for edge,line in self.edge_items.items():
            util = self.link_utilization.get(edge,{}).get("utilization",0)
            color = QColor("red") if util>=EDGE_UTIL_THRESHOLD else QColor("green")
            line.setPen(QPen(color,3))

        # Bar graph targets
        for i,node in enumerate(self.node_names):
            connected_edges = [k for k in self.link_utilization if node in k.split("<->")]
            max_util = max([self.link_utilization[e]["utilization"] for e in connected_edges],default=0)
            self.bar_target[i] = max_util*100

        # Average line
        avg_util = sum([self.link_utilization[e]["utilization"] for e in self.link_utilization])/max(1,len(self.link_utilization))
        self.avg_history.append(avg_util*100)
        self.line_curve.setData(list(self.avg_history))

        # Remove finished flows
        active_ids = [r["flow_id"] for r in state.get("routes",[])]
        for flow in self.flows[:]:
            if flow["flow_id"] not in active_ids:
                self.scene.removeItem(flow["graphics_item"])
                self.flows.remove(flow)

        # Add new flows
        for route in state.get("routes",[]):
            flow_id = route["flow_id"]
            if any(f['flow_id']==flow_id for f in self.flows): continue
            path_nodes = route.get("path",[])
            if len(path_nodes)<2: continue
            path_pts=[]
            for i in range(len(path_nodes)-1):
                path_pts += interpolate(pos_scaled[path_nodes[i]],pos_scaled[path_nodes[i+1]],STEPS_PER_FLOW)
            ellipse = QGraphicsEllipseItem(-FLOW_RADIUS,-FLOW_RADIUS,FLOW_RADIUS*2,FLOW_RADIUS*2)
            ellipse.setBrush(QBrush(QColor("blue")))
            ellipse.setPos(*path_pts[0])
            self.scene.addItem(ellipse)
            self.flows.append({"flow_id":flow_id,"path":path_pts,"current_step":0,"graphics_item":ellipse})

    # -----------------------------
    def update_animation(self):
        step_size = 2
        for flow in self.flows[:]:
            if flow["current_step"]<len(flow["path"]):
                x,y = flow["path"][flow["current_step"]]
                flow["graphics_item"].setPos(x,y)
                flow["current_step"] += 1
            else:
                self.scene.removeItem(flow["graphics_item"])
                self.flows.remove(flow)

        # Bar animation
        for i,target in enumerate(self.bar_target):
            if self.bar_displayed[i]<target:
                self.bar_displayed[i]=min(self.bar_displayed[i]+step_size,target)
            elif self.bar_displayed[i]>target:
                self.bar_displayed[i]=max(self.bar_displayed[i]-step_size,target)

        self.bar_widget.clear()
        for i,val in enumerate(self.bar_displayed):
            color = QColor(255,0,0) if val>=75 else QColor(0,255,0)
            bar = pg.BarGraphItem(x=[self.node_x[i]], height=[val], width=0.6, brush=color)
            self.bar_widget.addItem(bar)

    # -----------------------------
    # Control panel callbacks
    # -----------------------------
    def on_slider_change(self,value):
        self.slider_label.setText(f"Transfers: {value}")
        write_config(value,self.rl_checkbox.isChecked())

    def on_rl_change(self,state):
        write_config(self.slider.value(), self.rl_checkbox.isChecked())

# -----------------------------
# Run app
# -----------------------------
if __name__=="__main__":
    app = QApplication(sys.argv)
    dashboard = Dashboard()
    dashboard.show()
    sys.exit(app.exec())