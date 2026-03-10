from enum import Enum

default_schedule_config = {
    "0900": {"A": 0.5, "B": 1.0, "C": 1.0, "D": 1.0, "E": 1.0, "F": 1.0, "G": 0.5, "H": 1.0, "J": 1.0, "K": 1.0, "L": 1.0},
    "0930": {"A": 0.0, "B": 1.0, "C": 1.0, "D": 1.0, "E": 2.0, "F": 0.0, "G": 1.0, "H": 1.0, "J": 1.0, "K": 3.0, "L": 0.0},
    "1000": {"A": 2.0, "B": 0.0, "C": 1.0, "D": 2.0, "E": 1.0, "F": 0.0, "G": 0.5, "H": 0.0, "J": 2.0, "K": 1.5, "L": 0.0},
    "1030": {"A": 2.0, "B": 0.0, "C": 0.0, "D": 2.0, "E": 2.0, "F": 0.0, "G": 1.0, "H": 0.0, "J": 2.0, "K": 1.0, "L": 0.0},
    "1100": {"A": 2.0, "B": 0.0, "C": 0.0, "D": 2.0, "E": 1.0, "F": 1.0, "G": 3.0, "H": 0.5, "J": 0.0, "K": 0.0, "L": 0.5},
    "1130": {"A": 1.0, "B": 0.0, "C": 0.0, "D": 0.0, "E": 0.0, "F": 0.0, "G": 8.0, "H": 0.5, "J": 0.0, "K": 0.0, "L": 0.5},
    "1200": {"A": 2.0, "B": 0.0, "C": 0.0, "D": 0.0, "E": 0.0, "F": 0.0, "G": 8.0, "H": 0.0, "J": 0.0, "K": 0.0, "L": 0.0},
    "1230": {"A": 1.5, "B": 0.0, "C": 0.0, "D": 0.0, "E": 0.0, "F": 1.0, "G": 7.5, "H": 0.0, "J": 0.0, "K": 0.0, "L": 0.0},
    "0100": {"A": 2.0, "B": 1.0, "C": 1.0, "D": 0.0, "E": 0.0, "F": 0.0, "G": 6.0, "H": 0.0, "J": 0.0, "K": 0.0, "L": 0.0},
    "0130": {"A": 1.0, "B": 1.0, "C": 1.0, "D": 0.0, "E": 0.0, "F": 0.0, "G": 5.0, "H": 0.0, "J": 0.0, "K": 2.0, "L": 0.0},
    "0200": {"A": 0.0, "B": 1.0, "C": 1.0, "D": 0.0, "E": 0.0, "F": 0.0, "G": 4.0, "H": 0.0, "J": 2.0, "K": 2.0, "L": 0.0},
    "0230": {"A": 0.0, "B": 1.0, "C": 1.0, "D": 0.0, "E": 2.0, "F": 0.0, "G": 1.0, "H": 0.0, "J": 2.0, "K": 2.0, "L": 1.0},
    "0300": {"A": 0.0, "B": 0.0, "C": 0.0, "D": 2.0, "E": 2.0, "F": 1.0, "G": 0.0, "H": 0.0, "J": 2.0, "K": 2.0, "L": 1.0},
    "0330": {"A": 2.0, "B": 0.0, "C": 0.0, "D": 2.0, "E": 2.0, "F": 1.0, "G": 0.0, "H": 0.0, "J": 0.0, "K": 2.0, "L": 1.0},
    "0400": {"A": 1.0, "B": 0.0, "C": 0.0, "D": 2.0, "E": 2.0, "F": 2.0, "G": 0.0, "H": 0.0, "J": 0.0, "K": 2.0, "L": 1.0},
    "0430": {"A": 1.0, "B": 0.0, "C": 0.0, "D": 2.0, "E": 0.0, "F": 4.0, "G": 0.0, "H": 0.0, "J": 0.0, "K": 2.0, "L": 1.0},
    "0500": {"A": 3.0, "B": 0.0, "C": 0.0, "D": 0.0, "E": 0.0, "F": 5.0, "G": 0.0, "H": 0.0, "J": 0.0, "K": 2.0, "L": 0.0},
}

class Device_Type(Enum):
    DESKTOP = (50, 250)
    SERVER = (100, 500)
    PHONE = (10, 150)

class Network:
    def __init__(self, total_bandwidth):
        self.total_bandwidth = total_bandwidth
        self.global_devices = []
        self.global_nodes = []
    
    def simulate_day(schedule):
        pass

    def generate_devices(device_count):
        pass

    def assign_devices_to_nodes():
        pass
    
class Node:
    def __init__(self, max_bandwidth=1000):
        self.max_bandwidth = max_bandwidth
        self.local_devices = []
        
    def calculate_bandwidth_usage():
        usage = 0
        
        return usage
    
class Device:
    def __init__(self, device_type):
        self.device_type = device_type
        self.bandwidth_usage = self.calculate_bandwidth_usage(self.device_type)
    
    def calculate_bandwidth_usage(device_type):
        pass

def main():
    pass

if __name__ == "__main__":
    main()