"""Deterministic identity. uuid5 under a fixed project namespace, seeded from
spec paths, so recompiles are byte-stable and re-apply updates in place.

Renaming a chart mints a new UUID; apply deletes owned charts that leave the
spec, so renames propagate without leaving orphans.
"""

import uuid

# The identity seed is FROZEN at the project's original name: every dashboard
# and chart uuid ever created derives from it, so renaming it would make the
# tool refuse to touch its own dashboards. The product renamed; this cannot.
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "superset-dashboard-compiler")


def dashboard_uuid(slug: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, slug)


def chart_uuid(slug: str, chart_name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{slug}/chart/{chart_name}")
