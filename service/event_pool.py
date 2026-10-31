"""Immutable packaged random-event source, shared with local live-mode data."""
from functools import lru_cache
import json
import os
from pathlib import Path
import random

@lru_cache(maxsize=1)
def events():
    configured = os.environ.get('TEMPEST_EVENT_POOL')
    local = Path(__file__).with_name('random_events.json')
    path = Path(configured) if configured else (local if local.exists() else Path(__file__).with_name('example_events.json'))
    values=json.loads(path.read_text())['events']
    if not values or any(not isinstance(v,str) or not v.strip() or len(v)>1200 for v in values):
        raise ValueError('Invalid random-event pool')
    return tuple(values)

def sample():
    values=events()
    return {'event':random.SystemRandom().choice(values),'pool_size':len(values)}
