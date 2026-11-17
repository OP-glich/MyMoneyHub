"""Saved-record receipt content, shared by HTML and the copyable message."""
from decimal import Decimal

from .amounts import nad
from .identity import owner_name,lender_label


def reference(contract):
    return f'OSI-{contract.id:04d}'


def interest_label(contract):
    rate = format(Decimal(contract.rate_bps)/100, '.2f').rstrip('0').rstrip('.')
    return rate + ('% fixed for the loan term' if contract.basis == 'fixed' else '% per year, simple interest (actual days / 365)')


def whatsapp_message(contract, status, remaining=None):
    borrower = contract.person if contract.direction == 'lend' else owner_name()
    lender = lender_label() if contract.direction == 'lend' else contract.person
    pending = contract.loan_id is None or status in ('Pending','Pending Payout')
    lines = [
        'OSI LOAN — AGREEMENT DETAILS (NOT A PAYOUT RECEIPT)' if pending else 'OSI LOAN — PERSONAL LOAN ACKNOWLEDGEMENT', reference(contract),
        'Borrower: ' + borrower, 'Purpose: ' + (contract.purpose or 'Not recorded in the original loan'),
        'Principal: ' + nad(contract.principal), 'Interest rate: ' + interest_label(contract),
        'Interest: ' + nad(contract.interest),
        'Total repayment: ' + nad(contract.principal+contract.interest),
        'Date issued: ' + contract.start, 'Repayment due: ' + contract.due,
        'Lender: ' + lender,
        'Payout status: ' + ('Pending Payout — money has not been disbursed.' if pending else 'Money Paid Out'),
        'Loan status: ' + status,
    ]
    if remaining is not None:
        lines += ['Repaid: '+nad(contract.principal+contract.interest-remaining), 'Still to repay: '+nad(remaining)]
    return '\n'.join(lines)


def pdf_content(contract, status, payout_entry=None, remaining=None, payment=None, payment_entry=None):
    if not payment and payout_entry is None:
        raise ValueError('A payout receipt requires a verified payout record.')
    fields = [
        ('Loan date', contract.start), ('Repayment due date', contract.due),
        ('Original amount borrowed', nad(contract.principal)),
        ('Interest rate & basis', interest_label(contract)),
        ('Calculated interest', nad(contract.interest)),
        ('Lender', lender_label() if contract.direction == 'lend' else contract.person),
    ]
    if payment:
        fields += [('Payment date', payment_entry.date), ('Payment reference', payment.reference),
                   ('Amount received' if contract.direction == 'lend' else 'Amount paid', nad(payment.principal+payment.interest)),
                   ('Applied to interest / principal', nad(payment.interest)+' / '+nad(payment.principal))]
    elif payout_entry:
        fields.append(('Actual payout date', payout_entry.date))
        if remaining is not None:
            fields += [('Amount repaid',nad(contract.principal+contract.interest-remaining)),
                       ('Amount still owed',nad(remaining))]
    return dict(reference=reference(contract)+('-PAY-'+str(payment.id) if payment else ''),
                subtitle='REPAYMENT RECEIPT' if payment else ('PERSONAL LOAN ACKNOWLEDGEMENT' if contract.direction == 'lend' else 'PERSONAL BORROWING SUMMARY'),
                borrower=contract.person if contract.direction == 'lend' else owner_name(),
                purpose=contract.purpose or 'Purpose not recorded in the original loan', fields=fields,
                status='Repayment recorded' if payment else status,
                total_label='Remaining loan balance' if payment else 'Total amount to repay',
                total=nad(remaining if payment else contract.principal+contract.interest),
                total_note='After this repayment' if payment else 'Principal + agreed interest, in Namibian dollars',
                note='Payment recorded against the loan shown above.' if payment else 'Payout confirmed. A record of the agreed loan details and the actual money paid out.')
