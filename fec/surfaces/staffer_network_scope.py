"""Presentation and routing scope; retained evidence and v8 identities are untouched."""
import re

VERSION = 'network-scope-no-interns-v1'
_INTERN = re.compile(r'\bintern(?:s|ship)?\b', re.I)
_SUPERVISOR = re.compile(r'\b(?:coordinator|director|manager|supervisor|administrator)\b', re.I)


def intern_title(title):
    """Recognize a role, not an employer name or an intern programme supervisor."""
    title = str(title or '').strip()
    return bool(_INTERN.search(title) and not _SUPERVISOR.search(title))


def intern_roles(roles):
    # Do not erase a substantive parallel role, or infer an internship from a
    # person's name, an office compensation bucket, or an old biography.
    titles = [str(r.get('title') or '').strip() for r in roles or []]
    return bool(titles) and all(intern_title(title) for title in titles)


def intern_record(node, person=None):
    person = person or {}
    if person.get('latest_roles'):
        return intern_roles(person['latest_roles'])
    if person.get('current_roles'):
        return intern_roles(person['current_roles'])
    positions = node.get('current_positions') or []
    if positions:
        return intern_roles([{'title': p.split(' for ', 1)[0]} for p in positions])
    return intern_title(node.get('title'))
