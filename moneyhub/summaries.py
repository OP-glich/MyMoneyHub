"""One read model for Overview, budgets, savings, sales and reports.

All amounts come from committed ledger/debt/stock rows. Corrections are posted
reversals, so the same sums work for edited, cancelled and backdated entries.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from . import finance as f
from .receipts import whatsapp_message
from .loan_states import derive_state
from .models import (Account, Category, Entry, Budget, Saving, Loan, Repayment,
                     Product, Lot, Sale, MonthlyBudget, LoanContract, EntryRevision)


def percentage(spent, limit):
    if not limit: return '0'
    return format((Decimal(spent)*100/limit).quantize(Decimal('.01'), rounding=ROUND_HALF_UP), 'f').rstrip('0').rstrip('.') or '0'


def snapshot(s, month):
    f.day(month+'-01')
    accounts=list(s.scalars(select(Account).order_by(Account.id)))
    categories=list(s.scalars(select(Category).order_by(Category.kind,Category.name)))
    entries=list(s.scalars(select(Entry).order_by(Entry.date.desc(),Entry.id.desc())))
    loans=list(s.scalars(select(Loan).order_by(Loan.due)))
    payments=list(s.scalars(select(Repayment).order_by(Repayment.id.desc())))
    savings=list(s.scalars(select(Saving).order_by(Saving.id)))
    products=list(s.scalars(select(Product).order_by(Product.name)))
    lots=list(s.scalars(select(Lot).order_by(Lot.id.desc())))
    sales=list(s.scalars(select(Sale).order_by(Sale.id.desc())))
    contracts=list(s.scalars(select(LoanContract).order_by(LoanContract.id.desc())))
    revisions=list(s.scalars(select(EntryRevision)))
    entry_map={e.id:e for e in entries}
    loan_map={l.id:l for l in loans}
    balances={a.id:sum(e.amount for e in entries if e.account_id==a.id) for a in accounts}
    reserves={a.id:sum(g.amount for g in savings if g.account_id==a.id) for a in accounts}
    loan_balances={l.id:(l.principal-sum(p.principal for p in payments if p.loan_id==l.id),
                          l.interest-sum(p.interest for p in payments if p.loan_id==l.id)) for l in loans}
    loan_states={c.id:derive_state(c,loan_map.get(c.loan_id),
                                  entry_map.get(loan_map[c.loan_id].entry_id) if c.loan_id in loan_map else None,
                                  sum(loan_balances.get(c.loan_id,(0,0)))) for c in contracts}
    verified_ids={c.loan_id for c in contracts if loan_states[c.id].payout_verified}
    booked_loans=[l for l in loans if l.id in verified_ids]
    month_entries=[e for e in entries if e.date.startswith(month)]
    income=sum(e.amount for e in month_entries if e.kind=='income')
    expenses=-sum(e.amount for e in month_entries if e.kind=='expense')
    monthly_payments=[p for p in payments if entry_map[p.entry_id].date.startswith(month)]
    interest_income=sum(p.interest for p in monthly_payments if loan_map[p.loan_id].direction=='lend')
    interest_expense=sum(p.interest for p in monthly_payments if loan_map[p.loan_id].direction=='borrow')
    monthly_sales=[v for v in sales if entry_map[v.entry_id].date.startswith(month)]
    sales_revenue=sum(v.revenue for v in monthly_sales)
    cost_of_goods=sum(v.cost for v in monthly_sales)
    profit=sales_revenue-cost_of_goods
    spent=expenses+interest_expense
    received=income+interest_income+sales_revenue
    expense_by_category={c.id:-sum(e.amount for e in month_entries if e.kind=='expense' and e.category_id==c.id) for c in categories if c.kind=='expense'}
    budgets=list(s.scalars(select(Budget).where(Budget.month==month)))
    overall_budget=s.scalar(select(MonthlyBudget).where(MonthlyBudget.month==month))
    budget_limit=overall_budget.amount if overall_budget else sum(b.amount for b in budgets)
    budget_spent=spent if overall_budget else sum(expense_by_category[b.category_id] for b in budgets)
    budget_remaining=budget_limit-budget_spent if overall_budget or budgets else None
    cash=sum(balances.values())
    reserved_cash=sum(reserves.values())
    available=cash-reserved_cash
    everyday=f.default_account(s)
    everyday_cash=balances.get(everyday.id,0) if everyday else 0
    everyday_available=everyday_cash-(reserves.get(everyday.id,0) if everyday else 0)
    receivables=sum(loan_balances[l.id][0] for l in booked_loans if l.direction=='lend')
    projected=sum(loan_balances[l.id][1] for l in booked_loans if l.direction=='lend')
    owed_to_me=receivables+projected
    liabilities=sum(sum(loan_balances[l.id]) for l in booked_loans if l.direction=='borrow')
    principal_disbursed=sum(l.principal for l in booked_loans if l.direction=='lend')
    lender_ids={l.id for l in booked_loans if l.direction=='lend'}
    money_repaid=sum(p.principal+p.interest for p in payments if p.loan_id in lender_ids)
    interest_collected=sum(p.interest for p in payments if p.loan_id in lender_ids)
    statuses={c.id:loan_states[c.id].label for c in contracts}
    pending=[c for c in contracts if loan_states[c.id].code=='pending']
    alerts=sorted((c for c in contracts if loan_states[c.id].code in ('paid_out','overdue')),
                  key=lambda c:(c.due,c.id))[:5]
    monthly=[]
    for m in sorted({e.date[:7] for e in entries},reverse=True)[:12]:
        rows=[e for e in entries if e.date.startswith(m)]
        monthly.append((m,sum(e.amount for e in rows if e.kind=='income'),-sum(e.amount for e in rows if e.kind=='expense'),sum(e.amount for e in rows)))
    openings={a.id:sum(e.amount for e in entries if e.account_id==a.id and e.kind=='opening') for a in accounts}
    corrected={r.old_entry_id:r for r in revisions}
    correction_ids={r.reversal_entry_id for r in revisions}
    editable_ids={e.id for e in entries if e.kind in ('income','expense','opening') and e.id not in corrected and e.id not in correction_ids}
    return dict(accounts=accounts,categories=categories,entries=entries,loans=loans,payments=payments,
                savings=savings,products=products,lots=lots,sales=sales,contracts=contracts,
                entry_map=entry_map,loan_map=loan_map,balances=balances,reserves=reserves,
                loan_balances=loan_balances,month_entries=month_entries,income=income,expenses=expenses,
                interest_income=interest_income,interest_expense=interest_expense,monthly_sales=monthly_sales,
                uncategorized_expenses=-sum(e.amount for e in month_entries if e.kind=='expense' and not e.category_id),
                sales_revenue=sales_revenue,cost_of_goods=cost_of_goods,profit=profit,spent=spent,received=received,
                expense_by_category=expense_by_category,budgets=budgets,overall_budget=overall_budget,
                budget_limit=budget_limit,budget_spent=budget_spent,budget_remaining=budget_remaining,
                budget_percent=percentage(budget_spent,budget_limit),
                category_percent={b.id:percentage(expense_by_category[b.category_id],b.amount) for b in budgets},
                cash=cash,reserved_cash=reserved_cash,available=available,everyday=everyday,
                everyday_cash=everyday_cash,everyday_available=everyday_available,
                receivables=receivables,projected=projected,owed_to_me=owed_to_me,liabilities=liabilities,
                principal_disbursed=principal_disbursed,statuses=statuses,loan_states=loan_states,pending=pending,alerts=alerts,
                money_repaid=money_repaid,interest_collected=interest_collected,
                loan_messages={c.id:whatsapp_message(c,statuses[c.id],sum(loan_balances[c.loan_id])) for c in contracts if loan_states[c.id].payout_verified},
                monthly=monthly,category_map={c.id:c for c in categories},product_map={p.id:p for p in products},
                contract_by_loan={c.loan_id:c for c in contracts if c.loan_id},
                quantities={p.id:sum(l.remaining for l in lots if l.product_id==p.id) for p in products},
                sold_units={p.id:sum(v.quantity for v in sales if v.product_id==p.id) for p in products},
                product_profit={p.id:sum(v.revenue-v.cost for v in sales if v.product_id==p.id) for p in products},
                inventory_value=sum(l.remaining*l.unit_cost for l in lots),openings=openings,
                corrected=corrected,correction_ids=correction_ids,editable_ids=editable_ids)
