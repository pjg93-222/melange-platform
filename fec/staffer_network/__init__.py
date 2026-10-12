# -*- coding: utf-8 -*-
"""Typed, bitemporal evidence infrastructure for the staffer network.

Default import exposes contracts and validation primitives only.  Explicit
normalization entry points may read authorized restricted paths; importing the
package never opens them and never arms a loader.
"""

SCHEMA_VERSION = "staffer-network-v1"
