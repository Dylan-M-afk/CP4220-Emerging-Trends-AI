from fastapi import FastAPI
import json
from pathlib import Path

app = FastAPI()

STATE_FILE = Path("project/shared/state.json")

@app.get("/state")
def get_state():
    with open(STATE_FILE) as f:
        return json.load(f)
    
    