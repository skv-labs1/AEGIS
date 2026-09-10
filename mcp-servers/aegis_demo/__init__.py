"""Synthetic enterprise systems for the Aegis demo, exposed as MCP servers.

Nothing in this package is real. All users, devices, assets and incidents are
invented. See docs/ARCHITECTURE.md section 5 for the data-handling model: the
seed files in ``aegis_demo/seed`` are the source of truth and the SQLite
database is a disposable runtime cache rebuilt from them.
"""

__version__ = "0.1.0"
