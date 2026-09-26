"""Resolve legacy absolute local paths without rewriting frozen provider inputs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = '/Users/asanchez/DevWorkspace/omni-agent-platform'


def relocate(value):
    if isinstance(value, str) and (value == LEGACY_ROOT or value.startswith(LEGACY_ROOT + '/')):
        return str(ROOT) + value[len(LEGACY_ROOT):]
    if isinstance(value, list):
        return [relocate(item) for item in value]
    if isinstance(value, dict):
        return {relocate(key): item if key == 'migrated_from' else relocate(item) for key, item in value.items()}
    return value
