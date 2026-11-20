import sqlite3
from dataclasses import dataclass

from archive.records import ReportRecord


@dataclass(frozen=True)
class Receipt:
    report_number: str
    token: str


class ReceiptStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS receipts (
                report_number TEXT NOT NULL PRIMARY KEY REFERENCES reports(number),
                token TEXT NOT NULL UNIQUE
            )
        ''')

    def create_first(self, report: ReportRecord) -> Receipt:
        receipt = Receipt(report.number, f'ACK-{report.number}')
        self.db.execute('INSERT INTO receipts VALUES (?, ?)',
                        (receipt.report_number, receipt.token))
        return receipt

    def get(self, number: str) -> Receipt | None:
        row = self.db.execute('SELECT * FROM receipts WHERE report_number = ?', (number,)).fetchone()
        return None if row is None else Receipt(*row)
