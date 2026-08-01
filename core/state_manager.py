"""
Meridian State Manager — Persistent Memory Engine.
Saves and loads bot state, open pair positions, and partial exit tracking to JSON.
Protects against lost state during python restarts, crashes, or system reboots.
"""

import json
import os
import tempfile
import threading
import time
from typing import Dict, Any, Optional

STATE_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "meridian_state.json")


class StateManager:
    """
    Thread-safe persistent state manager with atomic file writing for Windows compatibility.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self):
        with self.lock:
            if os.path.exists(STATE_FILE_PATH):
                try:
                    with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
                        self.state = json.load(f)
                except Exception as e:
                    print(f"[STATE] Error loading persistent memory: {e}")
                    self.state = {}
            else:
                self.state = {}

    def save(self):
        with self.lock:
            try:
                dir_name = os.path.dirname(STATE_FILE_PATH)
                os.makedirs(dir_name, exist_ok=True)
                fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="state_", suffix=".tmp")
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        json.dump(self.state, f, indent=4)
                    
                    # Atomic replace with retry for Windows concurrency
                    for attempt in range(5):
                        try:
                            os.replace(temp_path, STATE_FILE_PATH)
                            break
                        except OSError as replace_err:
                            if attempt == 4:
                                raise replace_err
                            time.sleep(0.05)
                except Exception as e:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    raise e
            except Exception as e:
                print(f"[STATE] Error saving persistent memory: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return self.state.get(key, default)

    def set(self, key: str, value: Any):
        with self.lock:
            if self.state.get(key) == value:
                return
            self.state[key] = value
            self.save()

    def update_positions(self, open_positions: list):
        """Saves current open positions snapshot to persistent storage."""
        with self.lock:
            self.set("open_positions", open_positions)
            self.set("last_update", time.strftime('%Y-%m-%d %H:%M:%S'))


# Global singleton instance
meridian_state = StateManager()
