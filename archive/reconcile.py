from dataclasses import dataclass

from archive.receipts import Receipt
from archive.records import ReportRecord
from assessment.coverage import SlotState


@dataclass(frozen=True)
class IssuedReport:
    report: ReportRecord
    receipt: Receipt

    @property
    def number(self) -> str:
        return self.report.number


def reconcile_report(report: ReportRecord, receipt: Receipt | None) -> IssuedReport:
    if receipt is None or receipt.report_number != report.number or not receipt.token:
        raise ValueError('report does not have its first receipt')
    worksheet = report.worksheet
    section = worksheet.section
    if len(worksheet.rows) != section.last_slot - section.first_slot + 1:
        raise ValueError('report coverage does not match its fixed interval')
    valid_count = 0
    for slot, row in enumerate(worksheet.rows, section.first_slot):
        if row.slot != slot:
            raise ValueError('report rows do not match original slot order')
        source = row.provenance
        if row.state is SlotState.VALID:
            if (source is None or source.recording_id != section.recording.recording_id
                    or source.slot != slot or row.raw is None or row.micrometres is None):
                raise ValueError('report row does not have matching original evidence')
            valid_count += 1
        elif row.state is not SlotState.MISSING or any(
                value is not None for value in (source, row.raw, row.micrometres)):
            raise ValueError('report contains an unfinished or malformed missing row')
    if valid_count != worksheet.totals.valid_count:
        raise ValueError('report sample count does not match its valid rows')
    return IssuedReport(report, receipt)
