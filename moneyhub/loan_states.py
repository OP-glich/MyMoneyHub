"""Loan presentation is derived from financial evidence, never a saved toggle."""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LoanState:
    code: str
    label: str
    payout_verified: bool
    needs_review: bool = False

    @property
    def css(self):
        return {'pending':'pending','paid_out':'active','overdue':'overdue','repaid':'paid'}[self.code]

    @property
    def section(self):
        return 'given_out' if self.code in ('paid_out','overdue') else self.code


def payout_matches(contract, loan, entry):
    if loan is None or entry is None:
        return False
    return (contract.loan_id == loan.id and loan.entry_id == entry.id
            and contract.account_id == loan.account_id == entry.account_id
            and contract.principal == loan.principal and contract.interest == loan.interest
            and contract.direction == loan.direction and contract.start == loan.start
            and contract.due == loan.due and entry.kind == 'loan'
            and entry.amount == (-loan.principal if loan.direction == 'lend' else loan.principal)
            and entry.date >= loan.start)


def derive_state(contract, loan, entry, remaining, today=None):
    if not payout_matches(contract,loan,entry):
        return LoanState('pending','Pending Payout',False,contract.loan_id is not None)
    if remaining <= 0:
        return LoanState('repaid','Repaid With Interest' if contract.interest else 'Repaid in Full',True)
    if contract.due < (today or date.today().isoformat()):
        return LoanState('overdue','Overdue',True)
    return LoanState('paid_out','Given Out — Awaiting Repayment',True)


def filter_code(value):
    """Keep old bookmarked filters working without retaining old status labels."""
    return {'Pending':'pending','Pending Payout':'pending','Active':'paid_out',
            'Money Paid Out':'paid_out','Paid':'repaid','Repaid With Interest':'repaid',
            'Repaid in Full':'repaid','Overdue':'overdue'}.get(value,value or '')
