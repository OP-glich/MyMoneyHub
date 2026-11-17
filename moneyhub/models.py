from sqlalchemy import (Column, Integer, String, ForeignKey, CheckConstraint,
                        UniqueConstraint)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Owner(Base):
    __tablename__ = 'owners'
    id = Column(Integer, primary_key=True)
    password_hash = Column(String, nullable=False)


class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)


class Category(Base):
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    kind = Column(String, nullable=False)
    __table_args__ = (CheckConstraint("kind IN ('income','expense')"),)


class Operation(Base):
    __tablename__ = 'operations'
    id = Column(String(100), primary_key=True)
    kind = Column(String, nullable=False)


class Entry(Base):
    __tablename__ = 'entries'
    id = Column(Integer, primary_key=True)
    account_id = Column(ForeignKey('accounts.id'), nullable=False)
    category_id = Column(ForeignKey('categories.id'))
    operation_id = Column(ForeignKey('operations.id'), nullable=False)
    date = Column(String(10), nullable=False)
    kind = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    note = Column(String(500), nullable=False, default='')
    source_id = Column(String(200), unique=True)
    __table_args__ = (CheckConstraint('amount != 0'),)


class Budget(Base):
    __tablename__ = 'budgets'
    id = Column(Integer, primary_key=True)
    category_id = Column(ForeignKey('categories.id'), nullable=False)
    month = Column(String(7), nullable=False)
    amount = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('category_id', 'month'),
                      CheckConstraint('amount > 0'))


class Saving(Base):
    __tablename__ = 'savings'
    id = Column(Integer, primary_key=True)
    account_id = Column(ForeignKey('accounts.id'), nullable=False)
    name = Column(String(100), nullable=False)
    amount = Column(Integer, nullable=False)
    target = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint('amount >= 0 AND target > 0 AND amount <= target'),)


class Loan(Base):
    __tablename__ = 'loans'
    id = Column(Integer, primary_key=True)
    account_id = Column(ForeignKey('accounts.id'), nullable=False)
    entry_id = Column(ForeignKey('entries.id'), nullable=False, unique=True)
    direction = Column(String, nullable=False)
    person = Column(String(100), nullable=False)
    principal = Column(Integer, nullable=False)
    rate_bps = Column(Integer, nullable=False)
    term_days = Column(Integer, nullable=False)
    interest = Column(Integer, nullable=False)
    start = Column(String(10), nullable=False)
    due = Column(String(10), nullable=False)
    terms = Column(String(500), nullable=False)
    __table_args__ = (CheckConstraint("direction IN ('lend','borrow')"),
                      CheckConstraint('principal > 0 AND interest >= 0 AND rate_bps >= 0 AND term_days > 0'))


class Repayment(Base):
    __tablename__ = 'repayments'
    id = Column(Integer, primary_key=True)
    loan_id = Column(ForeignKey('loans.id'), nullable=False)
    entry_id = Column(ForeignKey('entries.id'), nullable=False, unique=True)
    reference = Column(String(100), nullable=False)
    principal = Column(Integer, nullable=False)
    interest = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('loan_id', 'reference'),
                      CheckConstraint('principal >= 0 AND interest >= 0 AND principal + interest > 0'))


class Product(Base):
    __tablename__ = 'products'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    selling_price = Column(Integer, nullable=False, default=0)


class Lot(Base):
    __tablename__ = 'lots'
    id = Column(Integer, primary_key=True)
    product_id = Column(ForeignKey('products.id'), nullable=False)
    entry_id = Column(ForeignKey('entries.id'), nullable=False, unique=True)
    quantity = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    unit_cost = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint('quantity > 0 AND remaining >= 0 AND remaining <= quantity AND unit_cost > 0'),)


class Sale(Base):
    __tablename__ = 'sales'
    id = Column(Integer, primary_key=True)
    product_id = Column(ForeignKey('products.id'), nullable=False)
    entry_id = Column(ForeignKey('entries.id'), nullable=False, unique=True)
    quantity = Column(Integer, nullable=False)
    revenue = Column(Integer, nullable=False)
    cost = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint('quantity > 0 AND revenue > 0 AND cost >= 0'),)


class Allocation(Base):
    __tablename__ = 'allocations'
    id = Column(Integer, primary_key=True)
    sale_id = Column(ForeignKey('sales.id'), nullable=False)
    lot_id = Column(ForeignKey('lots.id'), nullable=False)
    quantity = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint('quantity > 0'), UniqueConstraint('sale_id', 'lot_id'))


class Preference(Base):
    __tablename__ = 'preferences'
    id = Column(Integer, primary_key=True)
    account_id = Column(ForeignKey('accounts.id'), nullable=False)


class MonthlyBudget(Base):
    __tablename__ = 'monthly_budgets'
    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, unique=True)
    amount = Column(Integer, nullable=False)


class LoanContract(Base):
    __tablename__ = 'loan_contracts'
    id = Column(Integer, primary_key=True)
    loan_id = Column(ForeignKey('loans.id'), unique=True)
    account_id = Column(ForeignKey('accounts.id'), nullable=False)
    direction = Column(String, nullable=False)
    person = Column(String(100), nullable=False)
    contact = Column(String(100), nullable=False, default='')
    purpose = Column(String(500), nullable=False)
    principal = Column(Integer, nullable=False)
    rate_bps = Column(Integer, nullable=False)
    basis = Column(String, nullable=False, default='fixed')
    interest = Column(Integer, nullable=False)
    start = Column(String(10), nullable=False)
    due = Column(String(10), nullable=False)
    terms = Column(String(500), nullable=False, default='')


class EntryRevision(Base):
    __tablename__ = 'entry_revisions'
    id = Column(Integer, primary_key=True)
    old_entry_id = Column(ForeignKey('entries.id'), nullable=False, unique=True)
    reversal_entry_id = Column(ForeignKey('entries.id'), nullable=False, unique=True)
    replacement_entry_id = Column(ForeignKey('entries.id'), unique=True)
    operation_id = Column(ForeignKey('operations.id'), nullable=False, unique=True)
    created_at = Column(String(40), nullable=False)
    reason = Column(String(500), nullable=False)


class ReceiptDeletion(Base):
    __tablename__ = 'receipt_deletions'
    id = Column(Integer, primary_key=True)
    contract_id = Column(ForeignKey('loan_contracts.id'), unique=True)
    repayment_id = Column(ForeignKey('repayments.id'), unique=True)
    deleted_at = Column(String(40), nullable=False)
    __table_args__ = (CheckConstraint('(contract_id IS NOT NULL AND repayment_id IS NULL) OR (contract_id IS NULL AND repayment_id IS NOT NULL)'),)
