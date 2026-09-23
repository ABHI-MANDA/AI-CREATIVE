"""Deployment entry point for the modular backend boundary.

The root module remains the compatibility entry point for existing scripts and
tests; this wrapper gives hosting platforms an explicit backend import path.
"""

from app import app, socketio

__all__ = ["app", "socketio"]
