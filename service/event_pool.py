"""Immutable packaged random-event source, shared with local live-mode data."""
from functools import lru_cache
import json
from pathlib import Path
import random

@lru_cache(maxsize=1)
def events():
    values=json.loads(Path(__file__).with_name('random_events.json').read_text())['events']
    if not values or any(not isinstance(v,str) or not v.strip() or len(v)>1200 for v in values):
        raise ValueError('Invalid random-event pool')
    return tuple(values)

def sample():
    values=events()
    return {'event':random.SystemRandom().choice(values),'pool_size':len(values)}
