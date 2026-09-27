"""
auth.py
-------
Lightweight session-based auth. Not meant to replace a real identity
provider in production, but enough to demonstrate role separation
(attendant vs manager) as called for in the "extend next" list of v1.
"""

from functools import wraps
from flask import session, redirect, url_for, flash, request


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login", next=request.path))
            if session.get("role") not in roles:
                flash("You don't have permission to view that page.", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator
