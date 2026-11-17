"""Acceptance flows over a real Windows process and real rendered user forms.

These tests never seed money, call domain write services, construct operation
keys, or bypass authentication. SQLite connections are read-only assertions.
"""
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode

from http_workflows import live_app, Page
from test_receipt_pdf import pdf_text


def text(page): return '\n'.join(page.text)


def saved(page):
    assert 'Saved successfully.' in text(page), text(page)
    return page


def read_db(root, sql, args=()):
    path=(Path(root)/'moneyhub.db').resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        return c.execute(sql,args).fetchall()


def dump(root):
    path=(Path(root)/'moneyhub.db').resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        return '\n'.join(c.iterdump())


def metric(b, path, **expected):
    page=b.page(path)
    for name,value in expected.items(): assert page.metric(name.replace('_','-'))==value,(path,name,page.metrics)
    return page


def category(page, action, label, **matching):
    form=page.find_form(action,**matching)
    control=next(c for c in form['controls'] if c.get('name')=='category_id')
    return next(o['value'] for o in control['options'] if o['text'].strip()==label)


def card(page, contract):
    # Extract the actual visible saved record, not a JSON or hidden data object.
    # Page stores source so selected-card associations can be checked independently.
    match=re.search(r'<article id="loan-'+str(contract)+r'".*?</article>',page.html,re.S)
    assert match,'Saved loan card absent'
    return Page(match.group(0))


def get_contract(page):
    match=re.search(r'<article id="loan-(\d+)"',page.html)
    assert match, 'Saved agreement card missing'
    return int(match.group(1))


def test_exact_user_acceptance_a_to_l_and_real_launcher_restart(tmp_path):
    root=tmp_path/'acceptance-personal'
    launcher=Path(__file__).resolve().parents[1]/'Start MyMoney Hub.cmd'
    launcher_hash=hashlib.sha256(launcher.read_bytes()).hexdigest()
    with live_app(root) as b:
        b.signup()
        # A: the actual Settings opening form has no name/account-type choice.
        settings=b.page('/settings')
        opening=settings.find_form('/action/opening')
        assert not any(c.get('name') in ('name','account_id','account_type') for c in opening['controls'])
        saved(b.submit(settings,'/action/opening',dict(amount='1000')))
        metric(b,'/dashboard',cash='N$ 1,000.00',safe='N$ 1,000.00')
        # B: actual income form, including its real token/key/select defaults.
        saved(b.submit(b.page('/transactions?add=income'),'/action/transaction',
                       dict(amount='500',note='Income acceptance'),matching=dict(kind='income')))
        metric(b,'/dashboard',cash='N$ 1,500.00',received='N$ 500.00')
        # Establish both overall and category budgets. They must not add twice.
        invalid_budget=b.submit(b.page('/budgets'),'/action/monthly_budget',dict(amount='0'),validate=False)
        assert 'Amount is outside the allowed range' in text(invalid_budget)
        assert read_db(root,'SELECT COUNT(*) FROM monthly_budgets')==[(0,)]
        assert invalid_budget.values(invalid_budget.find_form('/action/monthly_budget'))['amount']=='0'
        saved(b.submit(invalid_budget,'/action/monthly_budget',dict(amount='2000')))
        budgets=b.page('/budgets')
        food=category(budgets,'/action/budget','Food')
        saved(b.submit(budgets,'/action/budget',dict(amount='500',category_id=food)))
        # C.
        expense_page=b.page('/transactions?add=expense')
        saved(b.submit(expense_page,'/action/transaction',dict(amount='200',category_id=food,note='Food acceptance'),matching=dict(kind='expense')))
        metric(b,'/dashboard',cash='N$ 1,300.00',spent='N$ 200.00',budget_remaining='N$ 1,800.00')
        metric(b,'/budgets',budget_spent='N$ 200.00',budget_remaining='N$ 1,800.00',budget_percent='10%')
        # D: preview through the real form's actual Preview submit button, no JS
        # arithmetic. Purpose is deliberately blank, as an ordinary user may do.
        due=(date.today()+timedelta(days=30)).isoformat()
        loan_form=b.page('/loans?add=loan')
        preview=b.submit(loan_form,'/action/loan_plan',dict(person='Acceptance borrower',amount='300',rate='10',due=due),submitter='/loans/preview')
        assert 'N$ 30.00' in text(preview) and 'N$ 330.00' in text(preview)
        assert read_db(root,'SELECT COUNT(*) FROM loan_contracts')==[(0,)]
        loan_page=saved(b.submit(preview,'/action/loan_plan'))
        contract=get_contract(loan_page)
        pending_card=card(loan_page,contract)
        assert 'Pending' in text(pending_card)
        assert pending_card.metric('interest')=='N$ 30.00'
        assert pending_card.metric('expected')=='N$ 330.00'
        assert pending_card.metric('outstanding')=='N$ 0.00'
        overview=metric(b,'/dashboard',cash='N$ 1,300.00',principal_disbursed='N$ 0.00',owed='N$ 0.00')
        assert 'Acceptance borrower' in text(overview) and 'Pending Payout agreements' in text(overview)
        # E: real paid-out form and an exact repeat of that successful submission.
        saved(b.submit(loan_page,'/action/payout',matching=dict(contract_id=contract)))
        payout_post=b.last_post
        assert 'already has a payout' in text(b.repeat())
        metric(b,'/dashboard',cash='N$ 1,000.00',principal_disbursed='N$ 300.00',owed='N$ 330.00')
        assert read_db(root,"SELECT COUNT(*) FROM entries WHERE kind='loan'")==[(1,)]
        # F: real repayment form. Extract the selected loan's actual hidden ID.
        loan_page=b.page('/loans')
        repayment=loan_page.find_form('/action/repayment')
        loan_id=loan_page.values(repayment)['loan_id']
        saved(b.submit(loan_page,'/action/repayment',dict(amount='110',reference='ACCEPTANCE-110'),matching=dict(loan_id=loan_id)))
        assert 'already recorded' in text(b.repeat())
        metric(b,'/dashboard',cash='N$ 1,110.00',owed='N$ 220.00',received='N$ 530.00')
        assert read_db(root,'SELECT principal,interest FROM repayments')==[(8000,3000)]
        assert card(b.page('/loans'),contract).metric('outstanding')=='N$ 220.00'
        # G and H.
        saved(b.submit(b.page('/savings'),'/action/saving',dict(name='Protected cushion',amount='400',target='800')))
        overview=metric(b,'/dashboard',cash='N$ 1,110.00',safe='N$ 710.00',protected='N$ 400.00')
        assert 'Acceptance borrower' in text(overview) and due in text(overview)
        metric(b,'/savings',cash='N$ 1,110.00',safe='N$ 710.00',protected='N$ 400.00')
        assert 'Food acceptance' in text(b.page('/transactions'))
        assert 'Saved successfully.' not in text(b.page('/transactions'))  # flashes are one-use
        # I: all four actions must be beside the selected saved record.
        loan_card=card(b.page('/loans'),contract)
        assert {'View Receipt','Record Repayment'} <= set(loan_card.text)
        assert 'Download PDF Receipt' in loan_card.text
        receipt_view=b.page(f'/loans/{contract}/receipt')
        assert {'Download PDF','Download Text','Copy WhatsApp Message'} <= set(receipt_view.text)
        pdf,pdf_headers=b.download(f'/loans/{contract}/receipt.pdf')
        txt,txt_headers=b.download(f'/loans/{contract}/receipt.txt')
        assert pdf_headers['Content-Type']=='application/pdf'
        assert f'OSI-{contract:04d}.pdf' in pdf_headers['Content-Disposition']
        assert pdf.startswith(b'%PDF-') and pdf.rstrip().endswith(b'%%EOF')
        assert txt_headers['Content-Type']=='text/plain; charset=utf-8'
        assert f'OSI-{contract:04d}.txt' in txt_headers['Content-Disposition']
        content=txt.decode('utf-8')
        for expected in ['OSI LOAN',f'OSI-{contract:04d}','Acceptance borrower','N$ 300.00','10% fixed','N$ 30.00','N$ 330.00','N$ 220.00','Given Out — Awaiting Repayment']:
            assert expected in content
        assert content.strip()==receipt_view.textareas['whatsapp-message'].strip()
        (root/'acceptance-receipt.pdf').write_bytes(pdf)
        (root/'acceptance-receipt.txt').write_bytes(txt)
        extracted=pdf_text(pdf)
        for expected in ['Acceptance borrower','N$ 300.00','N$ 30.00','N$ 330.00','Local Lender — OSI LOAN']: assert expected in extracted
        payment_receipt=next(a['href'] for a in b.page('/loans').links if re.fullmatch(r'/repayments/\d+/receipt',a.get('href','')))
        assert 'N$ 110.00' in text(b.page(payment_receipt)) and 'N$ 220.00' in text(b.page(payment_receipt))
        payment_pdf,payment_pdf_headers=b.download(payment_receipt+'.pdf')
        payment_txt,payment_txt_headers=b.download(payment_receipt+'.txt')
        assert payment_pdf_headers['Content-Type']=='application/pdf'
        assert payment_txt_headers['Content-Type']=='text/plain; charset=utf-8'
        for received in [pdf_text(payment_pdf),payment_txt.decode('utf-8')]:
            for expected in ['REPAYMENT RECEIPT','N$ 110.00','N$ 220.00','ACCEPTANCE-110']:
                assert expected in received
        # L: a rejected overpayment does not alter any database row. Keep input.
        before_invalid=dump(root)
        failed=b.submit(b.page('/loans'),'/action/repayment',dict(amount='221',reference='INVALID'),matching=dict(loan_id=loan_id))
        assert 'exceeds the outstanding' in text(failed)
        assert failed.values(failed.find_form('/action/repayment',loan_id=loan_id))['amount']=='221'
        assert dump(root)==before_invalid
        # Save full state and actual download before restart.
        before_restart=dump(root)
        ledger=b.get('/export/ledger.csv')
        (root/'acceptance-requests.json').write_text(json.dumps(b.requests,indent=2),encoding='utf-8')
    # J: launch the unchanged .cmd itself in a new process using isolated env.
    with live_app(root,launcher=True) as b:
        b.signin()
        metric(b,'/dashboard',cash='N$ 1,110.00',safe='N$ 710.00',protected='N$ 400.00',owed='N$ 220.00')
        assert dump(root)==before_restart
        assert b.get('/export/ledger.csv')==ledger
        assert b.download(f'/loans/{contract}/receipt.txt')[0]==txt
        assert b.download(f'/loans/{contract}/receipt.pdf')[0]==pdf
        for path in ['/dashboard','/transactions','/budgets','/savings','/loans','/sales','/reports','/settings']:
            assert 'MyMoney Hub' in text(b.page(path))
        # K: replaying the original payout after restart also cannot duplicate it.
        b.last_post=payout_post
        assert 'form has expired' in text(b.repeat())
        # Reuse the saved operation key with a current, rendered form token to
        # check the accounting guard independently of the session's CSRF guard.
        page=b.page('/loans')
        current_csrf=next(page.values(f)['csrf'] for f in page.forms if 'csrf' in page.values(f))
        fields=dict(parse_qsl(payout_post[1].decode()))
        fields['csrf']=current_csrf
        b.last_post=(payout_post[0],urlencode(fields).encode())
        assert 'already has a payout' in text(b.repeat())
        assert read_db(root,"SELECT COUNT(*) FROM entries WHERE kind='loan'")==[(1,)]
    assert hashlib.sha256(launcher.read_bytes()).hexdigest()==launcher_hash


def test_backdated_edit_cancel_budget_boundaries_and_errors_via_forms(tmp_path):
    root=tmp_path/'edit-personal'
    with live_app(root) as b:
        b.signup()
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='1000')))
        saved(b.submit(b.page('/budgets'),'/action/monthly_budget',dict(amount='2000')))
        page=b.page('/transactions')
        food=category(page,'/action/transaction','Food',kind='expense')
        result=saved(b.submit(page,'/action/transaction',dict(amount='500',category_id=food,note='To correct'),matching=dict(kind='expense')))
        metric(b,'/budgets',budget_spent='N$ 500.00',budget_remaining='N$ 1,500.00',budget_percent='25%')
        edit_link=next(a['href'] for a in result.links if re.fullmatch(r'/transactions/\d+/edit',a.get('href','')) and '/1/' not in a['href'])
        editor=b.page(edit_link)
        previous=(date.today().replace(day=1)-timedelta(days=1)).isoformat()
        saved(b.submit(editor,'/action/entry_edit',dict(amount='200',date=previous,note='Moved to previous month')))
        metric(b,'/dashboard',cash='N$ 800.00',spent='N$ 0.00',budget_remaining='N$ 2,000.00')
        assert 'N$ 200.00' in text(b.page('/reports?month='+previous[:7]))
        saved(b.submit(b.page('/budgets?month='+previous[:7]),'/action/monthly_budget',dict(amount='2000')))
        metric(b,'/budgets?month='+previous[:7],budget_spent='N$ 200.00',budget_remaining='N$ 1,800.00',budget_percent='10%')
        latest_link=next(a['href'] for a in b.page('/transactions').links if re.fullmatch(r'/transactions/\d+/edit',a.get('href','')) and '/1/' not in a['href'])
        editor=b.page(latest_link)
        unchanged=dump(root)
        failed=b.submit(editor,'/action/entry_edit',dict(amount='1200'),validate=False)
        assert 'overdraw' in text(failed)
        assert failed.values(failed.find_form('/action/entry_edit'))['amount']=='1200'
        assert dump(root)==unchanged
        # Cancel is an explicit checked user action and keeps all original rows.
        saved(b.submit(b.page(latest_link),'/action/entry_cancel',dict(confirm='yes')))
        metric(b,'/dashboard',cash='N$ 1,000.00',spent='N$ 0.00')
        assert read_db(root,'SELECT COUNT(*) FROM entry_revisions')==[(2,)]
        assert read_db(root,"SELECT COUNT(*) FROM entries WHERE note='To correct'")==[(1,)]
        assert 'Cancelled' in text(b.page('/transactions'))
        # Savings release changes safe-to-spend, never actual cash or income.
        saved(b.submit(b.page('/savings'),'/action/saving',dict(name='Cushion',amount='400',target='500')))
        metric(b,'/dashboard',cash='N$ 1,000.00',safe='N$ 600.00',received='N$ 0.00')
        page=b.page('/savings')
        goal=next(page.values(f)['id'] for f in page.forms if f.get('action')=='/action/saving' and 'id' in page.values(f))
        saved(b.submit(page,'/action/saving',dict(amount='0'),matching=dict(id=goal)))
        metric(b,'/dashboard',cash='N$ 1,000.00',safe='N$ 1,000.00',received='N$ 0.00')


def test_borrowing_stock_sales_and_transfers_use_saved_balances(tmp_path):
    root=tmp_path/'business-personal'
    with live_app(root) as b:
        b.signup()
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='1000')))
        due=(date.today()+timedelta(days=30)).isoformat()
        loans=saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person='Lender for business',amount='200',
                              rate='10',due=due,direction='borrow')))
        contract=get_contract(loans)
        saved(b.submit(loans,'/action/payout',matching=dict(contract_id=contract)))
        metric(b,'/dashboard',cash='N$ 1,200.00',received='N$ 0.00',owed='N$ 0.00',principal_disbursed='N$ 0.00')
        loans=b.page('/loans')
        loan_id=loans.values(loans.find_form('/action/repayment'))['loan_id']
        saved(b.submit(loans,'/action/repayment',dict(amount='100',reference='BORROWED-100'),matching=dict(loan_id=loan_id)))
        metric(b,'/dashboard',cash='N$ 1,100.00',received='N$ 0.00',spent='N$ 20.00')
        assert read_db(root,'SELECT principal,interest FROM repayments')==[(8000,2000)]
        saved(b.submit(b.page('/sales'),'/action/item',dict(name='Notebooks',quantity='5',amount='10.10',selling_price='20')))
        metric(b,'/dashboard',cash='N$ 1,049.50',spent='N$ 20.00')
        sales=b.page('/sales')
        product=sales.values(sales.find_form('/action/sale'))['product_id']
        saved(b.submit(sales,'/action/sale',dict(quantity='2',amount='20'),matching=dict(product_id=product)))
        metric(b,'/dashboard',cash='N$ 1,089.50',received='N$ 40.00',spent='N$ 20.00')
        metric(b,'/sales',sales_revenue='N$ 40.00',cost_of_goods='N$ 20.20',profit='N$ 19.80',inventory_value='N$ 30.30')
        assert read_db(root,'SELECT remaining FROM lots')==[(3,)]
        before=dump(root)
        failed=b.submit(b.page('/sales'),'/action/sale',dict(quantity='4',amount='20'),matching=dict(product_id=product),validate=False)
        assert 'exceeds available stock' in text(failed)
        assert failed.values(failed.find_form('/action/sale',product_id=product))['quantity']=='4'
        assert dump(root)==before
        # Explicit advanced transfer: it is never ordinary income or spending.
        saved(b.submit(b.page('/settings'),'/action/account',dict(name='Separate saved cash',amount='0')))
        settings=b.page('/settings')
        form=settings.find_form('/action/transfer')
        controls={c.get('name'):c for c in form['controls'] if c.get('name')}
        source=controls['account_id']['options'][1]['value']
        destination=controls['destination']['options'][2]['value']
        saved(b.submit(settings,'/action/transfer',dict(account_id=source,destination=destination,amount='100')))
        metric(b,'/dashboard',cash='N$ 1,089.50',safe='N$ 1,089.50',received='N$ 40.00',spent='N$ 20.00')
        assert read_db(root,'SELECT account_id,SUM(amount) FROM entries GROUP BY account_id')==[(int(source),98950),(int(destination),10000)]
        saved(b.submit(b.page('/transactions'),'/action/transaction',dict(amount='10'),matching=dict(kind='income')))
        metric(b,'/dashboard',cash='N$ 1,099.50',received='N$ 50.00')
        assert read_db(root,'SELECT account_id FROM preferences')==[(int(source),)]
        due=(date.today()+timedelta(days=30)).isoformat()
        planned=saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person='Current balance borrower',amount='50',rate='10',due=due)))
        planned_id=get_contract(planned)
        saved(b.submit(b.page('/settings'),'/action/default_balance',dict(balance_id=destination)))
        saved(b.submit(b.page('/loans?status=pending'),'/action/payout',matching=dict(contract_id=planned_id)))
        metric(b,'/dashboard',cash='N$ 1,049.50',owed='N$ 55.00')
        assert read_db(root,'SELECT account_id FROM loans WHERE direction=\'lend\'')==[(int(destination),)]
        assert read_db(root,'SELECT account_id,SUM(amount) FROM entries GROUP BY account_id')==[(int(source),99950),(int(destination),5000)]


def test_loan_downloads_belong_to_selected_saved_record(tmp_path):
    root=tmp_path/'receipt-association'
    with live_app(root) as b:
        b.signup()
        # Creating pending loans first must not make starting-money setup fail.
        due=(date.today()+timedelta(days=20)).isoformat()
        refs=[]
        for borrower,amount,rate in [('First borrower','1000','20'),('Second borrower','300','10')]:
            page=saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person=borrower,amount=amount,rate=rate,due=due)))
            refs.append(get_contract(page))
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='2000')))
        metric(b,'/dashboard',cash='N$ 2,000.00',owed='N$ 0.00',principal_disbursed='N$ 0.00')
        assert read_db(root,'SELECT COUNT(*) FROM accounts')==[(1,)]
        duplicate=b.submit(b.page('/settings'),'/action/opening',dict(amount='1000'))
        assert 'already recorded' in text(duplicate)
        assert read_db(root,"SELECT COUNT(*) FROM entries WHERE kind='opening'")==[(1,)]
        for ident in refs:
            saved(b.submit(b.page('/loans?status=pending'),'/action/payout',matching=dict(contract_id=ident)))
        for ident,name,other,expected in [(refs[0],'First borrower','Second borrower','N$ 1,200.00'),
                                          (refs[1],'Second borrower','First borrower','N$ 330.00')]:
            selected=b.page(f'/loans/{ident}/receipt')
            txt,headers=b.download(f'/loans/{ident}/receipt.txt')
            message=txt.decode('utf-8')
            assert name in message and other not in message and expected in message
            assert f'OSI-{ident:04d}.txt' in headers['Content-Disposition']
            assert 'Given Out — Awaiting Repayment' in message
            pdf,headers=b.download(f'/loans/{ident}/receipt.pdf')
            contents=pdf_text(pdf)
            assert name in contents and other not in contents and expected in contents and 'Given Out — Awaiting Repayment' in contents


def test_existing_v2_booked_loan_is_preserved_and_visible_over_http(tmp_path):
    """Legacy fixture only; sign-in and all reads use the actual HTTP app."""
    from moneyhub.db import migrate_connection, validate_database
    from werkzeug.security import generate_password_hash
    root=tmp_path/'legacy-personal'
    root.mkdir()
    path=root/'moneyhub.db'
    start=date.today().isoformat()
    due=(date.today()+timedelta(days=30)).isoformat()
    password_hash=generate_password_hash('UserWorkflowPassword!')
    with closing(sqlite3.connect(path)) as c:
        migrate_connection(c,target_version=2)
        c.execute('INSERT INTO owners VALUES(1,?)',(password_hash,))
        c.execute("INSERT INTO accounts VALUES(1,'Existing balance')")
        c.execute("INSERT INTO operations VALUES('original','opening'),('loan','loan')")
        c.execute("INSERT INTO entries(id,account_id,operation_id,date,kind,amount,note) VALUES(1,1,'original',?,'opening',100000,'Original money')",(start,))
        c.execute("INSERT INTO entries(id,account_id,operation_id,date,kind,amount,note) VALUES(2,1,'loan',?,'loan',-30000,'Original payout')",(start,))
        c.execute("INSERT INTO loans VALUES(1,1,2,'lend','Legacy borrower',30000,1000,30,3000,?,?,'Original terms')",(start,due))
        c.commit()
        before={t:c.execute('SELECT * FROM '+t).fetchall() for t in ['owners','accounts','operations','entries','loans']}
    with live_app(root) as b:
        b.signin()
        page=b.page('/loans')
        assert 'Legacy borrower' in text(page) and 'Original terms' in text(page)
        metric(b,'/dashboard',cash='N$ 700.00',owed='N$ 330.00',principal_disbursed='N$ 300.00')
        for table,rows in before.items(): assert read_db(root,'SELECT * FROM '+table)==rows
        assert read_db(root,'SELECT loan_id FROM loan_contracts')==[(1,)]
        backup=next((root/'backups').glob('before-upgrade-v2-*.db'))
        validate_database(backup)
        with closing(sqlite3.connect(backup)) as c:
            assert c.execute('PRAGMA user_version').fetchone()[0]==2
