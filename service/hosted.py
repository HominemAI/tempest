"""Authenticated adapters for the existing live-model and index interfaces.

After authentication, server.py delegates GET requests to resource(path,
principal), returning (bytes, content_type), and API requests to dispatch(method,
path, query, principal). None means the caller should continue normal routing.
No research corpus or locally maintained simulation/history is exposed here.
"""
import json
from functools import lru_cache
from pathlib import Path

from access import Forbidden
from core import ROOT, active_mapping


PAPERS = ROOT / 'papers'
WEB = Path(__file__).parent / 'web'
ASSETS = {
    '/assets/hosted.js': (WEB / 'hosted.js', 'text/javascript; charset=utf-8'),
    '/assets/inference.js': (PAPERS / 'web/inference.js', 'text/javascript; charset=utf-8'),
    '/assets/inference.css': (PAPERS / 'web/inference.css', 'text/css; charset=utf-8'),
    '/assets/pressure.js': (PAPERS / 'web/pressure.js', 'text/javascript; charset=utf-8'),
    '/assets/relationship-multipliers.json': (PAPERS / 'web/relationship-multipliers.json', 'application/json'),
    '/assets/state-index.js': (PAPERS / 'web/state-index.js', 'text/javascript; charset=utf-8'),
    '/assets/state-index.css': (PAPERS / 'web/state-index.css', 'text/css; charset=utf-8'),
    '/assets/theme.js': (PAPERS / 'web/theme.js', 'text/javascript; charset=utf-8'),
    '/assets/tempest.css': (PAPERS / 'web/tempest.css', 'text/css; charset=utf-8'),
}

@lru_cache(maxsize=2)
def _state_index(kind):
    # Published indexes are immutable for the lifetime of the running image.
    return json.loads(active_mapping(kind).read_text())


def _admin(principal):
    if principal.get('kind') != 'admin':
        raise Forbidden('Administrator access required')


def _navigation(active):
    links = [('Test model', '/inference'), ('State index', '/state-index'), ('Admin', '/admin')]
    return '<a class="tempest-skip" href="#tempest-content">Skip to workspace</a><nav class="tempest-nav" aria-label="Tempest workspace"><a class="tempest-brand" href="/inference">Tempest</a><div class="tempest-sections">' + ''.join(
        f'<a href="{url}"' + (' aria-current="page"' if url == active else '') + f'>{label}</a>'
        for label, url in links) + '</div><button id="tempest-theme" type="button" aria-pressed="false">Dark mode</button></nav>'


def _frame(source, path):
    source = source.replace('<html lang="en">', '<html lang="en" data-theme="light" data-tempest-theme>')
    source = source.replace('<head>', '<head><script src="/assets/theme.js"></script><meta name="color-scheme" content="light dark">')
    source = source.replace('<link rel="stylesheet" href="/assets/inference.css">',
                            '<link rel="stylesheet" href="/assets/inference.css"><link rel="stylesheet" href="/assets/tempest.css">')
    body = '<body data-tempest-area="inference">' if path == '/inference' else '<body>'
    source = source.replace('<body>', body + _navigation(path))
    return source.replace('<main id="tempest-content">', '<main id="tempest-content" tabindex="-1">')


def inference_page():
    source = (PAPERS / 'inference.html').read_text()
    source = source.replace('<script src="/assets/inference.js" defer></script>',
                            '<script src="/assets/hosted.js" defer></script><script src="/assets/inference.js" defer></script>')
    source = _frame(source, '/inference')
    replacements = {
        'Test model · Tempest': 'Live model · Tempest',
        '<h1>Test model</h1>': '<h1>Live model</h1>',
        'Local Tempest inference · 512-token checkpoint · no API calls': 'Hosted Tempest inference · saved personalities and server history',
        'Save state &amp; settings': 'Save settings',
        'Load a saved baseline into a fresh live session. Loading restores saved state and settings. Save state &amp; settings updates the selected personality; its original baseline is kept.': 'Load a saved personality to continue from its current server state. Each event saves automatically. Save settings applies your control changes.',
        '>Reset session</button>': '>Create from selection</button><button id="start-neutral" type="button">Start neutral (all 0s)</button>',
        'Ready. Events are randomly sampled from the local corpus; no cloud calls or saved profile edits.': 'Ready. Play samples the packaged local event corpus. Inference runs on the server and every turn is saved.',
        'Press Play or enter an event below.': 'Load a saved personality or enter an event below to create one.',
        'Newest first · loaded personality across sessions, or this session when unsaved. Each completed turn is saved automatically. Full state history is stored below.': 'Newest first · saved turns for the selected personality. Each completed event is stored on the server.',
        'Pauses automatic playback and applies one turn.': 'Applies and saves one turn on the server.',
        'Bars show accumulated state, clamped to −10…+10. Target cadence: one event per second after warm-up; slow requests run sequentially without a backlog. Hidden tabs pause automatically.': 'Bars show server state, clamped to −10…+10. Recovery, category effects, and baseline shifts are applied by the service.',
        'Completed turns are stored locally across reloads. Reset starts a new session; earlier sessions remain available.': 'Completed turns are stored on the server across reloads. New saved instance creates a separate personality; earlier instances remain available.',
        '>Refresh sessions</button>': '>Refresh instances</button>',
        '<label>Session<select id="history-session">': '<label>Personality<select id="history-session">',
        'Choose a session': 'Choose a personality',
        'Download session JSON': 'Download history JSON',
        'Settings apply next turn and remain when resetting the session.': 'Settings save before the next event. New instances use the displayed settings.',
        'Reset session keeps these settings.': 'New instances use the displayed settings.',
        'style="max-width:75ch;line-height:1.65" ': '',
        'onclick="document.getElementById(\'event-turn-details\').hidden=true"': 'id="event-details-close"',
    }
    # The longer original status must be replaced before its button-label text.
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        source = source.replace(old, new)
    return source.encode()


def resource(path, principal):
    if path not in ASSETS and path not in ('/inference', '/state-index'):
        return None
    _admin(principal)
    if path in ASSETS:
        filename, content_type = ASSETS[path]
        return filename.read_bytes(), content_type
    if path == '/inference':
        return inference_page(), 'text/html; charset=utf-8'
    source = (PAPERS / 'state-index.html').read_text()
    source = source.replace('<script src="/assets/state-index.js" defer></script>',
                            '<script src="/assets/hosted.js" defer></script><script src="/assets/state-index.js" defer></script>')
    source = _frame(source, '/state-index')
    return source.encode(), 'text/html; charset=utf-8'


def dispatch(method, path, query, principal):
    if path != '/api/state-index':
        return None
    _admin(principal)
    if method != 'GET':
        raise ValueError('State indexes are read-only')
    kind = query.get('kind', ['state'])[0]
    if kind not in ('state', 'delta'):
        raise ValueError('kind must be state or delta')
    return _state_index(kind)
