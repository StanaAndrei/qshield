"""Dashboard page modules."""

from __future__ import annotations

from dashboard.pages.experiments import render_experiments_page
from dashboard.pages.operations import render_operations_page

__all__ = [
    "render_experiments_page",
    "render_operations_page",
]
