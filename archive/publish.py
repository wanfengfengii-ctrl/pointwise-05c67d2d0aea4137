import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from archive.receipts import ReceiptStore
from archive.reconcile import IssuedReport, reconcile_report
from archive.records import ReportStore
from assessment.worksheet import build_worksheet
from capture.outcomes import SlotEvidence
from device.recorder import SlotAvailability
from shift.sections import SectionRegistry


@dataclass(frozen=True)
class PendingIssue:
    section_id: str
    slots: tuple[int, ...]


class Publisher:
    def __init__(self, db: sqlite3.Connection, sections: SectionRegistry,
                 reports: ReportStore, receipts: ReceiptStore,
                 evidence_at: Callable[[str, int], SlotEvidence],
                 availability_at: Callable[[str, int], SlotAvailability]):
        self.db = db
        self.sections = sections
        self.reports = reports
        self.receipts = receipts
        self.evidence_at = evidence_at
        self.availability_at = availability_at

    def issue(self, section_id: str) -> IssuedReport | PendingIssue:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            section = self.sections.get_section(section_id)
            if section is None:
                raise KeyError(section_id)
            existing = self.reports.find_by_section(section_id)
            if existing is not None:
                return reconcile_report(existing, self.receipts.get(existing.number))
            worksheet = build_worksheet(section, self.evidence_at, self.availability_at)
            pending = worksheet.pending_slots
            if pending:
                return PendingIssue(section_id, pending)
            report = self.reports.save_report(worksheet)
            receipt = self.receipts.create_first(report)
            issued = reconcile_report(report, receipt)
        return issued
