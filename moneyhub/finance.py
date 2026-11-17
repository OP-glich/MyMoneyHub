"""All monetary writes go through this transaction-scoped domain service."""
import csv
import io
import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select, func

from .models import (Account, Category, Operation, Entry, Budget, Saving, Loan,
                     Repayment, Product, Lot, Sale, Allocation, Preference,
                     MonthlyBudget, LoanContract, EntryRevision)
from .amounts import money, nad, MAX_CENTS, loan_interest, split_payment, fifo_plan
from .loan_states import derive_state


def day(value):
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError('Enter a valid date.') from None
    if parsed.isoformat() != value:
        raise ValueError('Use YYYY-MM-DD dates.')
    return value


def posted_day(value):
    value = day(value)
    if value > date.today().isoformat():
        raise ValueError('Cash transactions cannot be future dated.')
    return value


def name(value, limit=100):
    value = str(value or '').strip()
    if not value or len(value) > limit:
        raise ValueError(f'Enter text between 1 and {limit} characters.')
    return value


def integer(value, minimum=1, maximum=1_000_000):
    if not re.fullmatch(r'\d+', str(value)):
        raise ValueError('Enter a whole number.')
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError('Whole number is outside the allowed range.')
    return result


def get(s, model, ident):
    obj = s.get(model, integer(ident, maximum=2**63-1))
    if obj is None:
        raise ValueError('Selected record does not exist.')
    return obj


def total(s, model, column, *where):
    return s.scalar(select(func.coalesce(func.sum(column), 0)).select_from(model).where(*where))


def balance(s, account_id):
    return total(s, Entry, Entry.amount, Entry.account_id == account_id)


def reserved(s, account_id):
    return total(s, Saving, Saving.amount, Saving.account_id == account_id)


def spendable(s, account_id):
    return balance(s, account_id) - reserved(s, account_id)


def operation(s, key, kind):
    key = name(key)
    if s.get(Operation, key):
        raise ValueError('This submission has already been recorded. Refresh the page.')
    s.add(Operation(id=key, kind=kind))
    s.flush()
    return key


def entry(s, account_id, amount, kind, when, note, op, category_id=None, source_id=None):
    account = get(s, Account, account_id)
    if amount == 0 or abs(amount) > MAX_CENTS:
        raise ValueError('Amount must be nonzero and within the allowed range.')
    if balance(s, account_id) + amount > MAX_CENTS:
        raise ValueError('Resulting account balance is outside the allowed range.')
    if amount < 0 and -amount > spendable(s, account_id):
        raise ValueError(f'Insufficient spendable cash in {account.name}: {nad(spendable(s, account_id))} available. '
                         'Release protected savings if needed, or move money from another saved balance in Settings.')
    row = Entry(account_id=account_id, amount=amount, kind=kind,
                date=posted_day(when), note=str(note or '').strip()[:500],
                operation_id=op, category_id=category_id, source_id=source_id)
    s.add(row)
    s.flush()
    return row


def create_account(s, account_name, opening, when, key):
    when = posted_day(when)
    opening = money(opening)
    if opening < 0:
        raise ValueError('Opening cash cannot be negative. Record borrowing separately.')
    op = operation(s, key, 'account')
    account = Account(name=name(account_name))
    s.add(account)
    s.flush()
    if opening:
        entry(s, account.id, opening, 'opening', when, 'Opening balance', op)
    return account


def transaction(s, account_id, category_id, amount, when, note, key, source_id=None):
    category = get(s, Category, category_id)
    amount = money(amount, positive=True)
    if source_id and s.scalar(select(Entry.id).where(Entry.source_id == source_id)):
        raise ValueError('CSV source ID was already imported.')
    op = operation(s, key, category.kind)
    return entry(s, account_id, amount if category.kind == 'income' else -amount,
                 category.kind, when, note, op, category.id, source_id)


def transfer(s, source, destination, amount, when, key):
    if source == destination:
        raise ValueError('Select different accounts.')
    amount = money(amount, positive=True)
    op = operation(s, key, 'transfer')
    entry(s, source, -amount, 'transfer', when, 'Transfer out', op)
    entry(s, destination, amount, 'transfer', when, 'Transfer in', op)


def saving(s, account_id, label, amount, target, key, ident=None):
    get(s, Account, account_id)
    amount, target = money(amount), money(target, positive=True)
    if not 0 <= amount <= target:
        raise ValueError('Reserved funds must be between zero and the target.')
    operation(s, key, 'saving')
    old = get(s, Saving, ident) if ident else None
    available = spendable(s, account_id) + (old.amount if old and old.account_id == account_id else 0)
    if amount > available:
        raise ValueError('Cannot reserve more than available cash.')
    if old:
        if old.account_id != account_id:
            raise ValueError('A savings goal cannot change accounts.')
        old.name, old.amount, old.target = name(label), amount, target
        return old
    row = Saving(account_id=account_id, name=name(label), amount=amount, target=target)
    s.add(row)
    return row


def budget(s, category_id, month, amount, key):
    if get(s, Category, category_id).kind != 'expense':
        raise ValueError('Budgets require an expense category.')
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('Use YYYY-MM for the budget month.')
    day(month + '-01')
    amount = money(amount, positive=True)
    operation(s, key, 'budget')
    row = s.scalar(select(Budget).where(Budget.category_id == category_id, Budget.month == month))
    if row:
        row.amount = amount
    else:
        s.add(Budget(category_id=category_id, month=month, amount=amount))


def create_loan(s, account_id, direction, person, principal, rate, term_days, start, due, terms, key,
                basis='fixed', contact='', purpose='', contract=None, payout_date=None):
    if direction not in ('lend', 'borrow'):
        raise ValueError('Choose lending or borrowing.')
    principal = money(principal, positive=True)
    rate_bps = money(rate)
    if not 0 <= rate_bps <= 100_000:
        raise ValueError('Term interest rate must be between 0 and 1000 percent.')
    term_days = integer(term_days, maximum=36500)
    start, due = posted_day(start), day(due)
    if (date.fromisoformat(due) - date.fromisoformat(start)).days != term_days:
        raise ValueError('Due date must equal start date plus the agreed term in days.')
    interest = loan_interest(principal, rate_bps, basis, term_days)
    if principal + interest > MAX_CENTS:
        raise ValueError('Total loan amount is too large.')
    op = operation(s, key, 'loan')
    person = name(person)
    payout_date = posted_day(payout_date or start)
    if payout_date < start:
        raise ValueError('Payout cannot predate the loan agreement.')
    origination = entry(s, account_id, -principal if direction == 'lend' else principal,
                        'loan', payout_date, f'{direction.title()}: {person}', op)
    row = Loan(account_id=account_id, entry_id=origination.id, direction=direction, person=person,
               principal=principal, rate_bps=rate_bps, term_days=term_days,
               interest=interest, start=start, due=due, terms=name(terms, 500))
    s.add(row)
    s.flush()
    if contract is None:
        contract = LoanContract(account_id=account_id, direction=direction, person=person,
                                principal=principal, rate_bps=rate_bps, basis=basis,
                                interest=interest, start=start, due=due, terms=terms,
                                contact=str(contact or '').strip()[:100], purpose=name(purpose, 500) if purpose else '')
        s.add(contract)
    contract.loan_id = row.id
    s.flush()
    return row


def outstanding(s, loan):
    paid_p = total(s, Repayment, Repayment.principal, Repayment.loan_id == loan.id)
    paid_i = total(s, Repayment, Repayment.interest, Repayment.loan_id == loan.id)
    return loan.principal - paid_p, loan.interest - paid_i


def repay(s, loan_id, account_id, amount, when, reference, key, auto_reference=False):
    loan = get(s, Loan, loan_id)
    contract = s.scalar(select(LoanContract).where(LoanContract.loan_id == loan.id))
    if contract is None or not loan_state(s,contract).payout_verified:
        raise ValueError('The historical payout could not be verified. No repayment was recorded; review the original cash entry first.')
    when = posted_day(when)
    if when < loan.start:
        raise ValueError('Repayment cannot predate the loan.')
    if when < get(s, Entry, loan.entry_id).date:
        raise ValueError('Repayment cannot predate the actual payout.')
    latest = s.scalar(select(func.max(Entry.date)).join(Repayment, Repayment.entry_id == Entry.id).where(Repayment.loan_id == loan.id))
    if latest and when < latest:
        raise ValueError('Repayments must be recorded in date order for each loan.')
    reference = name(reference)
    if s.scalar(select(Repayment.id).where(Repayment.loan_id == loan.id, Repayment.reference == reference)):
        raise ValueError('This payment reference is already recorded for this loan.')
    amount = money(amount, positive=True)
    if auto_reference and s.scalar(select(Repayment.id).join(Entry, Repayment.entry_id == Entry.id).where(
            Repayment.loan_id == loan.id, Entry.date == when,
            Repayment.principal+Repayment.interest == amount)):
        raise ValueError('A repayment of this amount is already saved for this date. Add a unique payment reference only if this is a separate payment.')
    principal, interest = outstanding(s, loan)
    paid_principal, paid_interest = split_payment(amount, principal, interest)
    op = operation(s, key, 'repayment')
    row = entry(s, account_id, amount if loan.direction == 'lend' else -amount,
                'repayment', when, f'Payment: {loan.person} / {reference}', op)
    payment = Repayment(loan_id=loan.id, entry_id=row.id, reference=reference,
                        principal=paid_principal, interest=paid_interest)
    s.add(payment)
    s.flush()
    return payment


def stock(s, product_id):
    return total(s, Lot, Lot.remaining, Lot.product_id == product_id)


def inventory_day(s, product_id, when):
    when = posted_day(when)
    latest_purchase = s.scalar(select(func.max(Entry.date)).join(Lot, Lot.entry_id == Entry.id).where(Lot.product_id == product_id))
    latest_sale = s.scalar(select(func.max(Entry.date)).join(Sale, Sale.entry_id == Entry.id).where(Sale.product_id == product_id))
    latest = max(latest_purchase or '', latest_sale or '')
    if when < latest:
        raise ValueError('Record inventory movements in date order for each product.')
    return when


def purchase(s, product_id, account_id, quantity, unit_cost, when, key):
    product = get(s, Product, product_id)
    when = inventory_day(s, product_id, when)
    quantity, unit_cost = integer(quantity), money(unit_cost, positive=True)
    op = operation(s, key, 'purchase')
    row = entry(s, account_id, -quantity*unit_cost, 'purchase', when, 'Stock purchase: ' + product.name, op)
    lot = Lot(product_id=product.id, entry_id=row.id, quantity=quantity, remaining=quantity, unit_cost=unit_cost)
    s.add(lot)
    return lot


def sell(s, product_id, account_id, quantity, unit_price, when, key):
    product = get(s, Product, product_id)
    when = inventory_day(s, product_id, when)
    quantity, price = integer(quantity), money(unit_price, positive=True)
    if quantity > stock(s, product.id):
        raise ValueError('Sale exceeds available stock.')
    op = operation(s, key, 'sale')
    row = entry(s, account_id, quantity*price, 'sale', when, 'Sale: ' + product.name, op)
    sale = Sale(product_id=product.id, entry_id=row.id, quantity=quantity, revenue=quantity*price, cost=0)
    s.add(sale)
    s.flush()
    lots = list(s.scalars(select(Lot).join(Entry, Lot.entry_id == Entry.id).where(
            Lot.product_id == product.id, Lot.remaining > 0).order_by(Entry.date, Lot.id)))
    by_id = {lot.id: lot for lot in lots}
    plan = fifo_plan([(lot.id, lot.remaining, lot.unit_cost) for lot in lots], quantity)
    for lot_id, used, cost in plan:
        lot = by_id[lot_id]
        lot.remaining -= used
        sale.cost += cost
        s.add(Allocation(sale_id=sale.id, lot_id=lot.id, quantity=used))
    s.flush()
    return sale


CSV_FIELDS = ['source_id', 'date', 'account', 'category', 'amount', 'note']


def default_account(s, create=False):
    preference = s.get(Preference, 1)
    if preference:
        return get(s, Account, preference.account_id)
    account = s.scalar(select(Account).order_by(Account.id).limit(1))
    if account is None and create:
        import secrets
        account = create_account(s, 'Personal balance', '0', date.today().isoformat(), secrets.token_hex(20))
    if account and create:
        s.add(Preference(id=1, account_id=account.id))
        s.flush()
    return account


def set_default(s, account_id, key):
    account = get(s, Account, account_id)
    operation(s, key, 'preference')
    preference = s.get(Preference, 1)
    if preference:
        preference.account_id = account.id
    else:
        s.add(Preference(id=1, account_id=account.id))


def opening_balance(s, amount, when, key):
    """Fund the current personal balance, never silently create a second one."""
    account = default_account(s, create=True)
    originals = select(EntryRevision.old_entry_id)
    reversals = select(EntryRevision.reversal_entry_id)
    existing = s.scalar(select(Entry.id).where(Entry.account_id == account.id, Entry.kind == 'opening',
                         Entry.amount > 0, ~Entry.id.in_(originals), ~Entry.id.in_(reversals)))
    if existing:
        raise ValueError('Starting money is already recorded for this balance. Use Edit in Transactions to correct it; nothing was added twice.')
    op = operation(s, key, 'opening')
    return entry(s, account.id, money(amount, positive=True), 'opening', when, 'Starting money', op)


def editable_entry(s, ident):
    row = get(s, Entry, ident)
    if row.kind not in ('income', 'expense', 'opening'):
        raise ValueError('Only ordinary income, expenses and starting money can be corrected here. Loan and stock history must remain linked to their records.')
    if s.scalar(select(EntryRevision.id).where((EntryRevision.old_entry_id == row.id) | (EntryRevision.reversal_entry_id == row.id))):
        raise ValueError('This entry has already been corrected or cancelled. Open the latest entry in history.')
    if (row.kind == 'expense') != (row.amount < 0):
        raise ValueError('A correction entry cannot be edited directly.')
    return row


def revise_entry(s, ident, amount, when, category_id, note, key, cancel=False):
    """Append a reversal/replacement; never erase or overwrite a financial row."""
    from datetime import datetime, timezone
    old = editable_entry(s, ident)
    replacement_amount = 0
    category = old.category_id
    if not cancel:
        replacement_amount = money(amount, positive=True) * (-1 if old.kind == 'expense' else 1)
        when = posted_day(when)
        if old.kind != 'opening':
            category = integer(category_id) if category_id else old.category_id
            if category and get(s, Category, category).kind != old.kind:
                raise ValueError('Choose a category matching this entry.')
    final_cash = balance(s, old.account_id)+replacement_amount-old.amount
    if final_cash < reserved(s, old.account_id):
        raise ValueError('This change would overdraw the balance or spend protected savings. Release protected money or correct related entries first.')
    if final_cash > MAX_CENTS:
        raise ValueError('Resulting balance is outside the allowed range.')
    op = operation(s, key, 'entry_cancel' if cancel else 'entry_edit')
    # Validate the final net effect above. Intermediate legs are in one locked
    # transaction, so editing spent income does not require covering it twice.
    reversal = Entry(account_id=old.account_id, category_id=old.category_id, operation_id=op,
                     date=old.date, kind=old.kind, amount=-old.amount,
                     note=('Cancelled: ' if cancel else 'Corrected: ')+(old.note or old.kind))
    s.add(reversal)
    s.flush()
    replacement = None
    if not cancel:
        replacement = Entry(account_id=old.account_id, category_id=category, operation_id=op,
                            date=when, kind=old.kind, amount=replacement_amount,
                            note=str(note or '').strip()[:500])
        s.add(replacement)
        s.flush()
    s.add(EntryRevision(old_entry_id=old.id, reversal_entry_id=reversal.id,
                        replacement_entry_id=replacement.id if replacement else None,
                        operation_id=op, created_at=datetime.now(timezone.utc).isoformat(),
                        reason='Cancelled by user; original retained' if cancel else 'Corrected by user; original retained'))
    s.flush()
    return replacement or reversal


def simple_transaction(s, kind, amount, when, note, key, category_id=None, account_id=None):
    if kind not in ('income', 'expense'):
        raise ValueError('Choose income or expense.')
    account = get(s, Account, account_id) if account_id else default_account(s, create=True)
    if category_id:
        category = get(s, Category, category_id)
        if category.kind != kind:
            raise ValueError('Choose a category matching income or expense.')
    else:
        label = 'Other income' if kind == 'income' else 'Other expenses'
        category = s.scalar(select(Category).where(Category.name == label, Category.kind == kind))
        if category is None:
            # Legacy category names may exist with the opposite type.
            fallback = label if not s.scalar(select(Category.id).where(Category.name == label)) else label+' (default)'
            category = s.scalar(select(Category).where(Category.name == fallback, Category.kind == kind))
            if category is None:
                category = Category(name=fallback, kind=kind)
                s.add(category)
                s.flush()
    return transaction(s, account.id, category.id, amount, when, note, key)


def monthly_budget(s, month, amount, key):
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('Choose a valid month.')
    day(month+'-01')
    operation(s, key, 'monthly_budget')
    amount = money(amount, positive=True)
    row = s.scalar(select(MonthlyBudget).where(MonthlyBudget.month == month))
    if row:
        row.amount = amount
    else:
        s.add(MonthlyBudget(month=month, amount=amount))


def propose_loan(s, account_id, direction, person, principal, rate, start, due, purpose,
                 key, basis='fixed', contact='', terms=''):
    get(s, Account, account_id)
    if direction not in ('lend', 'borrow'):
        raise ValueError('Choose lending or borrowing.')
    start, due = day(start), day(due)
    days = (date.fromisoformat(due)-date.fromisoformat(start)).days
    if not 1 <= days <= 36500:
        raise ValueError('Due date must be after the loan date (maximum 100 years).')
    principal, rate_bps = money(principal, positive=True), money(rate)
    if not 0 <= rate_bps <= 100000:
        raise ValueError('Interest rate must be between 0 and 1000 percent.')
    interest = loan_interest(principal, rate_bps, basis, days)
    if principal+interest > MAX_CENTS:
        raise ValueError('Total loan amount is too large.')
    operation(s, key, 'agreement')
    row = LoanContract(account_id=account_id, direction=direction, person=name(person),
                        principal=principal, rate_bps=rate_bps, basis=basis, interest=interest,
                        start=start, due=due, purpose=str(purpose or '').strip()[:500],
                        contact=str(contact or '').strip()[:100], terms=str(terms or '').strip()[:500])
    s.add(row)
    s.flush()
    return row


def payout(s, contract_id, when, key, account_id=None):
    c = get(s, LoanContract, contract_id)
    if c.loan_id is not None:
        raise ValueError('This loan already has a payout. Nothing was recorded twice.')
    when = posted_day(when)
    if when < c.start:
        raise ValueError('Payout cannot predate the loan agreement.')
    if account_id is not None:
        c.account_id = get(s, Account, account_id).id
    days = (date.fromisoformat(c.due)-date.fromisoformat(c.start)).days
    return create_loan(s, c.account_id, c.direction, c.person, format(Decimal(c.principal)/100,'.2f'),
                       format(Decimal(c.rate_bps)/100,'.2f'), str(days), c.start, c.due,
                       c.terms or c.purpose or 'Agreed interest and repayment dates as recorded.', key,
                       basis=c.basis, contract=c, payout_date=when)


def loan_status(s, contract):
    return loan_state(s,contract).label


def loan_state(s, contract):
    loan = s.get(Loan,contract.loan_id) if contract.loan_id else None
    cash_entry = s.get(Entry,loan.entry_id) if loan else None
    return derive_state(contract,loan,cash_entry,sum(outstanding(s,loan)) if loan else 0)


def add_item(s, account_id, label, quantity, unit_cost, selling_price, when, key):
    price = money(selling_price, positive=True)
    product = s.scalar(select(Product).where(Product.name == name(label)))
    if product is None:
        product = Product(name=name(label), selling_price=price)
        s.add(product)
        s.flush()
    else:
        product.selling_price = price
    purchase(s, product.id, account_id, quantity, unit_cost, when, key)
    return product


def import_csv(s, content):
    """Strict additive format; entire file rolls back on any error."""
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    if reader.fieldnames != CSV_FIELDS:
        raise ValueError('CSV header must be: ' + ','.join(CSV_FIELDS))
    count = 0
    for number, row in enumerate(reader, 2):
        if number > 10001:
            raise ValueError('Import is limited to 10,000 rows.')
        try:
            if None in row or any(v is None for v in row.values()):
                raise ValueError('Incorrect column count.')
            source = name(row['source_id'], 200)
            account = s.scalar(select(Account).where(Account.name == row['account']))
            category = s.scalar(select(Category).where(Category.name == row['category']))
            if not account or not category:
                raise ValueError('Account and category must already exist.')
            transaction(s, account.id, category.id, row['amount'], row['date'], row['note'],
                        'csv:' + __import__('hashlib').sha256(source.encode()).hexdigest(), source)
            count += 1
        except ValueError as exc:
            raise ValueError(f'CSV row {number}: {exc}') from None
    if count == 0:
        raise ValueError('CSV contains no transactions.')
    return count


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


def export_csv(s, entries):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['id', 'date', 'account', 'category', 'kind', 'amount_nad', 'note'])
    for row in entries:
        writer.writerow([row.id, row.date, csv_safe(s.get(Account, row.account_id).name),
                         csv_safe(s.get(Category, row.category_id).name) if row.category_id else '',
                         row.kind, format(Decimal(row.amount)/100, '.2f'), csv_safe(row.note)])
    return out.getvalue()
