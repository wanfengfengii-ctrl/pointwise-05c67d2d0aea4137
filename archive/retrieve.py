from archive.receipts import ReceiptStore
from archive.reconcile import IssuedReport, reconcile_report
from archive.records import ReportStore


class Archive:
    def __init__(self, reports: ReportStore, receipts: ReceiptStore):
        self.reports = reports
        self.receipts = receipts

    def retrieve(self, number: str) -> IssuedReport | None:
        report = self.reports.find_by_number(number)
        if report is None:
            return None
        return reconcile_report(report, self.receipts.get(number))
