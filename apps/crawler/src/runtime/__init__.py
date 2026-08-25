"""Replaceable crawler runtime boundaries.

The modules in this package are deliberately small.  They isolate runtime
capabilities that can move to Go without moving queue, persistence, and
extraction semantics in the same change.
"""

from __future__ import annotations
