"""Select one removable private enterprise package at process startup."""
import json
import os
from pathlib import Path
import warnings

ROOT = Path(__file__).resolve().parent


def select_workspace(root, environ=None):
    """Return (directory, source), without creating files or importing plugins.

    Explicit service configuration fails closed. A stale local pointer falls
    back to generic so removing a private package remains reversible.
    """
    root = Path(root).resolve()
    environ = os.environ if environ is None else environ
    generic = root / 'enterprises' / 'default'
    explicit = environ.get('CARBON_ENTERPRISE_DIR', '').strip()
    if explicit:
        selected = (root / explicit).resolve()
        if selected != generic and not selected.is_dir():
            raise RuntimeError('Selected enterprise folder is missing: ' + str(selected))
        return selected, 'environment'
    selector = root / '.enterprise.local.json'
    if selector.is_file():
        with selector.open(encoding='utf-8-sig') as stream:
            selection = json.load(stream)['directory']
        if not isinstance(selection, str) or not selection.strip():
            raise ValueError('Local enterprise selection must contain a nonempty directory')
        selected = (root / selection).resolve()
        if selected == generic or selected.is_dir():
            return selected, 'local'
        warnings.warn('Local enterprise package was removed; starting generic mode with its separate data directory.', RuntimeWarning)
        return generic, 'removed-local-package'
    parent = root / 'enterprises'
    candidates = []
    if parent.is_dir():
        for candidate in sorted(parent.iterdir()):
            if candidate.name == 'default' or candidate.name.startswith(('_', '.')):
                continue
            marker = candidate / 'enterprise.json'
            if not candidate.is_dir() or not marker.is_file():
                continue
            with marker.open(encoding='utf-8-sig') as stream:
                metadata = json.load(stream)
            if metadata.get('id') != candidate.name or metadata.get('layout_version') != 1:
                raise ValueError('Invalid enterprise.json id or layout_version: ' + str(marker))
            candidates.append(candidate.resolve())
    if len(candidates) > 1:
        raise RuntimeError('Multiple enterprise packages found; select one using CARBON_ENTERPRISE_DIR or .enterprise.local.json')
    return (candidates[0], 'auto') if candidates else (generic, 'generic')


_workspace, SELECTION_SOURCE = select_workspace(ROOT)
ENTERPRISE_DIR = str(_workspace)
IS_GENERIC = _workspace == ROOT / 'enterprises' / 'default'
DATA_DIR = os.path.join(ENTERPRISE_DIR, 'data')
NORMALIZERS_DIR = os.path.join(ENTERPRISE_DIR, 'normalizers')
PRESET_PACKS_DIR = os.path.join(ENTERPRISE_DIR, 'presets_packs')
TEMPLATE_DIR = os.path.join(ENTERPRISE_DIR, 'templates', 'reports', 'docx')
