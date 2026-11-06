"""Conservative, read-only verification of a single auto-rotation goal.

No UI actions or settings writes live here.  Callers may mark a supported goal
complete ONLY when ``verify_rotation_goal(...)["matched"] is True.  Unknown
readings (``actual is None``) never match, including on command failures.
"""
import re
import subprocess
from typing import Any


ROTATION_QUERY = ('shell', 'settings', 'get', 'system', 'accelerometer_rotation')

# Full-request grammar, not keyword detection: extra tasks, questions, quoted
# instructions and negations are deliberately unsupported. Navigation is optional
# and limited to the user's explicit home/quick-entry route.
_ROUTE = (
    r'(?:在首页[,，](?:界面)?右上方下拉进入快捷入口[,，])?'
)
_OBJECT = r'(?:屏幕)?(?:自动旋转|自动旋转屏幕|屏幕自动旋转)'
_REQUEST = re.compile(
    r'\s*(?:所有手机执行操作[:：]\s*)?(?:请)?(?:帮我)?' + _ROUTE
    + r'(?:(?P<before>关闭|关掉|禁用|打开|开启|启用)' + _OBJECT
    + r'|(?:把|将)' + _OBJECT + r'(?P<after>关闭|关掉|禁用|打开|开启|启用))'
    + r'[。！!]?\s*'
)
_OFF = frozenset(('关闭', '关掉', '禁用'))


def rotation_goal(goal: str) -> int | None:
    """Return Android's expected 0 (off), 1 (on), or None (unsupported).

    Only an entire single-purpose request is accepted.  Rejecting an unfamiliar
    paraphrase is preferable to completing a compound or unrelated task early.
    """
    if not isinstance(goal, str):
        return None
    match = _REQUEST.fullmatch(goal)
    if match is None:
        return None
    verb = match.group('before') or match.group('after')
    return 0 if verb in _OFF else 1


def _adb_path() -> str:
    # Resolve the same executable as the rest of the web application, lazily so
    # importing this pure goal parser does not load the server.
    from .server import _adb_path as resolve
    return resolve()


def read_rotation(serial: str) -> dict[str, Any]:
    """Read only accelerometer_rotation using one fixed, shell-free ADB argv.

    Returns {actual: 0|1|None, evidence: {...}}.  Evidence includes the exact
    command, stdout/stderr, returncode, source and a stable error code.  Only
    stripped stdout exactly equal to '0' or '1' with returncode 0 is known.
    Failures and invalid serials are unknown, never an assumed disabled state.
    """
    evidence: dict[str, Any] = {
        'source': 'adb.settings.system.accelerometer_rotation',
        'command': None, 'stdout': '', 'stderr': '', 'returncode': None,
        'error': None,
    }
    result = {'actual': None, 'evidence': evidence}
    if (not isinstance(serial, str)
            or re.fullmatch(r'[A-Za-z0-9_\[][A-Za-z0-9_.:\[\]-]*', serial) is None):
        evidence['error'] = 'invalid_serial'
        return result
    try:
        command = [_adb_path(), '-s', serial, *ROTATION_QUERY]
        evidence['command'] = command
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=10, shell=False,
        )
        evidence.update(stdout=completed.stdout, stderr=completed.stderr,
                        returncode=completed.returncode)
        if completed.returncode != 0:
            evidence['error'] = 'adb_failed'
        elif completed.stdout.strip() in ('0', '1'):
            result['actual'] = int(completed.stdout.strip())
        else:
            evidence['error'] = 'unknown_value'
    except subprocess.TimeoutExpired as exc:
        evidence.update(error='timeout', stdout=_text(exc.stdout),
                        stderr=_text(exc.stderr))
    except (OSError, subprocess.SubprocessError) as exc:
        evidence.update(error='adb_unavailable', detail=str(exc))
    return result


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value or ''


def verify_rotation_goal(serial: str, goal: str) -> dict[str, Any]:
    """Return {supported, matched, expected, actual, evidence}.

    Unsupported requests do not query a device.  ``matched`` is always a bool;
    false covers both an observed mismatch and an unknown reading.  Inspect
    ``actual`` and evidence.error to distinguish these cases.  The observation
    proves the setting at read time, not that a requested UI route was followed.
    """
    expected = rotation_goal(goal)
    if expected is None:
        return {'supported': False, 'matched': False, 'expected': None,
                'actual': None, 'evidence': {'error': 'unsupported_goal'}}
    reading = read_rotation(serial)
    actual = reading['actual']
    return {'supported': True, 'matched': actual is not None and actual == expected,
            'expected': expected, 'actual': actual, 'evidence': reading['evidence']}
