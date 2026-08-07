"""DEPRECATED -- superseded by src/export/workbook.py.

The single-sheet exporter was replaced by the six-tab workbook (Shortlist,
All Postings, History, Changes, Summary, Run Stats). Kept only so an old import
fails loudly instead of silently writing the wrong thing.
"""
from __future__ import annotations


def write(*_args, **_kwargs):
    raise RuntimeError(
        "src.export.to_excel is deprecated. Use src.export.workbook.write() -- "
        "see run.py for the call shape. This file can be deleted."
    )
