"""Data update coordinators for TCL Lyon.

Scaffolded — implementation arrives in v0.3.

Two coordinators planned:

    class DeparturesCoordinator(DataUpdateCoordinator):
        '''Polls SIRI estimated-timetables per followed line, filters client-side
        to the configured stops. ~45s interval.'''

    class DisruptionsCoordinator(DataUpdateCoordinator):
        '''Polls SIRI situation-exchange in bulk, filters by configured lines.
        ~5min interval — disruptions don't move that fast.'''

The per-line polling decision comes from the POC: server respects ?LineRef= but
ignores ?MonitoringRef=. See docs/03-poc-findings.md.
"""

from __future__ import annotations
