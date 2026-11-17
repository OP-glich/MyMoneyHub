"""Focused loan acceptance through actual rendered forms and the real server."""
from contextlib import closing
from datetime import date, timedelta
from urllib.error import HTTPError
from pathlib import Path
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from http_workflows import live_app
from test_acceptance_http import saved, text, metric, card, get_contract, read_db, dump
from test_receipt_pdf import pdf_text


def assert_no_receipt(browser, ident):
    for suffix in ['', '.pdf', '.txt', '?pdf=1', '?format=txt']:
        with pytest.raises(HTTPError) as caught:
            browser.get(f'/loans/{ident}/receipt'+suffix)
        response=caught.value
        assert response.code==409
        assert response.headers.get_content_type()=='text/html'
        assert b'No payout receipt' in response.read()


def test_simple_payout_receipt_and_automatic_settlement(tmp_path):
    root=tmp_path/'lifecycle'
    with live_app(root) as b:
        b.signup()
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='2000')))
        due=(date.today()+timedelta(days=30)).isoformat()
        values=dict(person='Example Lifecycle',purpose='School supplies',amount='1000',rate='20',due=due)
        preview=b.submit(b.page('/loans?add=loan'),'/action/loan_plan',values,submitter='/loans/preview')
        assert 'N$ 200.00' in text(preview) and 'N$ 1,200.00' in text(preview)
        loans=saved(b.submit(preview,'/action/loan_plan'))
        ident=get_contract(loans)
        pending=card(loans,ident)
        assert pending.metric('status')=='Pending Payout'
        assert pending.metric('paid-out')=='N$ 0.00'
        assert pending.metric('interest')=='N$ 200.00'
        assert pending.metric('expected')=='N$ 1,200.00'
        assert not any('/receipt' in a.get('href','') for a in pending.links)
        assert 'pay_now' not in pending.html
        assert pending.find_form('/action/payout')
        assert 'Generate Loan & Receipt' in text(pending)
        metric(b,'/dashboard',cash='N$ 2,000.00',owed='N$ 0.00',principal_disbursed='N$ 0.00')
        before=dump(root)
        assert_no_receipt(b,ident)
        assert dump(root)==before

        receipt=saved(b.submit(loans,'/action/payout',matching=dict(contract_id=ident)))
        confirmation=b.last_post
        assert '/loans?saved=' in b.requests[-1][3]
        assert 'Download PDF Receipt' in text(receipt)
        assert 'Given Out — Awaiting Repayment' in text(receipt)
        metric(b,'/dashboard',cash='N$ 1,000.00',owed='N$ 1,200.00',principal_disbursed='N$ 1,000.00')
        paid_card=card(b.page('/loans'),ident)
        assert paid_card.metric('status')=='Given Out — Awaiting Repayment'
        assert not any(f.get('action')=='/action/payout' for f in paid_card.forms)
        assert 'Download PDF Receipt' in text(paid_card) and 'Copy WhatsApp Message' in text(paid_card)
        loan_id=paid_card.values(paid_card.find_form('/action/repayment'))['loan_id']

        # Viewing/printing (a read-only HTML view) and each download never posts money.
        before_reads=dump(root)
        for _ in range(2):
            b.page(f'/loans/{ident}/receipt')
            pdf,headers=b.download(f'/loans/{ident}/receipt.pdf')
            assert headers['Content-Type']=='application/pdf'
            assert 'Given Out — Awaiting Repayment' in pdf_text(pdf)
            txt,headers=b.download(f'/loans/{ident}/receipt.txt')
            assert 'Given Out — Awaiting Repayment' in txt.decode()
        assert dump(root)==before_reads
        b.last_post=confirmation
        assert 'already has a payout' in text(b.repeat())
        assert read_db(root,"SELECT COUNT(*) FROM entries WHERE kind='loan'")==[(1,)]

        loans=saved(b.submit(b.page('/loans'),'/action/repayment',dict(amount='500',reference='PART-500'),matching=dict(loan_id=loan_id)))
        partial=card(loans,ident)
        assert partial.metric('status')=='Given Out — Awaiting Repayment'
        assert partial.metric('repaid')=='N$ 500.00'
        assert partial.metric('outstanding')=='N$ 700.00'
        metric(b,'/dashboard',cash='N$ 1,500.00',owed='N$ 700.00',received='N$ 200.00')
        assert read_db(root,'SELECT principal,interest FROM repayments')==[(30000,20000)]
        assert 'already recorded' in text(b.repeat())
        loans=saved(b.submit(b.page('/loans'),'/action/repayment',dict(amount='700',reference='FINAL-700'),matching=dict(loan_id=loan_id)))
        complete=card(loans,ident)
        assert complete.metric('status')=='Repaid With Interest'
        assert complete.metric('outstanding')=='N$ 0.00'
        assert complete.metric('repaid')=='N$ 1,200.00'
        assert not any(f.get('action') in ('/action/payout','/action/repayment') for f in complete.forms)
        assert 'View Receipt' in text(complete) and 'View Payment History' in text(complete)
        assert read_db(root,'SELECT principal,interest FROM repayments')==[(30000,20000),(70000,0)]
        metric(b,'/dashboard',cash='N$ 2,200.00',owed='N$ 0.00',received='N$ 200.00')
        final_pdf=b.download(f'/loans/{ident}/receipt.pdf')[0]
        assert 'Repaid With Interest' in pdf_text(final_pdf)
        assert 'Amount still owed' in pdf_text(final_pdf)
        final_dump=dump(root)
        (root/'settled-receipt.pdf').write_bytes(final_pdf)
    with live_app(root) as b:
        b.signin()
        assert card(b.page('/loans?status=repaid'),ident).metric('status')=='Repaid With Interest'
        assert b.download(f'/loans/{ident}/receipt.pdf')[0]==final_pdf
        assert dump(root)==final_dump


def test_overdue_and_zero_interest_settlement_are_automatic(tmp_path):
    root=tmp_path/'overdue'
    with live_app(root) as b:
        b.signup()
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='2000')))
        values=dict(person='Past due borrower',amount='1000',rate='20',
                    date=(date.today()-timedelta(days=10)).isoformat(),due=(date.today()-timedelta(days=1)).isoformat())
        loans=saved(b.submit(b.page('/loans'),'/action/loan_plan',values))
        ident=get_contract(loans)
        assert card(loans,ident).metric('status')=='Pending Payout'
        saved(b.submit(loans,'/action/payout',matching=dict(contract_id=ident)))
        page=b.page('/loans')
        assert card(page,ident).metric('status')=='Overdue'
        loan_id=card(page,ident).values(card(page,ident).find_form('/action/repayment'))['loan_id']
        loans=saved(b.submit(page,'/action/repayment',dict(amount='500',reference='LATE-PART'),matching=dict(loan_id=loan_id)))
        assert card(loans,ident).metric('status')=='Overdue'
        assert card(loans,ident).metric('outstanding')=='N$ 700.00'
        assert 'Overdue' in text(b.page('/dashboard'))
        saved(b.submit(b.page('/loans'),'/action/repayment',dict(amount='700',reference='LATE-FINAL'),matching=dict(loan_id=loan_id)))
        assert card(b.page('/loans?status=repaid'),ident).metric('status')=='Repaid With Interest'
        loans=saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person='Interest free borrower',amount='100',rate='0',
                            due=(date.today()+timedelta(days=5)).isoformat())))
        free_id=get_contract(loans)
        saved(b.submit(loans,'/action/payout',matching=dict(contract_id=free_id)))
        page=b.page('/loans')
        free_card=card(page,free_id)
        free_loan=free_card.values(free_card.find_form('/action/repayment'))['loan_id']
        saved(b.submit(page,'/action/repayment',dict(amount='100',reference='FREE-FINAL'),matching=dict(loan_id=free_loan)))
        assert card(b.page('/loans?status=repaid'),free_id).metric('status')=='Repaid in Full'
        assert 'Repaid in Full' in pdf_text(b.download(f'/loans/{free_id}/receipt.pdf')[0])


def test_historical_document_without_matching_cash_is_not_payout_proof(tmp_path):
    from moneyhub.db import engine_for
    root=tmp_path/'historical'; root.mkdir()
    path=root/'moneyhub.db'
    engine=engine_for(path);engine.dispose()
    today=date.today().isoformat(); due=(date.today()+timedelta(days=30)).isoformat()
    with closing(sqlite3.connect(path)) as c:
        c.execute('INSERT INTO owners VALUES(1,?)',(generate_password_hash('UserWorkflowPassword!'),))
        c.execute("INSERT INTO accounts VALUES(1,'Existing money')")
        c.execute("INSERT INTO operations VALUES('opening','account')")
        c.execute("INSERT INTO entries(id,account_id,operation_id,date,kind,amount,note) VALUES(1,1,'opening',?,'opening',200000,'Real opening money')",(today,))
        # Legacy inconsistency: the loan links to an opening, not a payout.
        c.execute("INSERT INTO loans VALUES(1,1,1,'lend','Historical borrower',100000,2000,30,20000,?,?,'Preserve original terms')",(today,due))
        c.execute("INSERT INTO loan_contracts(id,loan_id,account_id,direction,person,purpose,principal,rate_bps,basis,interest,start,due,terms) VALUES(1,1,1,'lend','Historical borrower','Original agreement',100000,2000,'fixed',20000,?,?,'Preserve original terms')",(today,due))
        c.commit()
    before=dump(root)
    with live_app(root) as b:
        b.signin()
        historical=card(b.page('/loans?status=pending'),1)
        assert historical.metric('status')=='Pending Payout'
        assert 'Historical payout needs verification' in text(historical)
        assert not any(f.get('action')=='/action/payout' for f in historical.forms)
        assert_no_receipt(b,1)
        metric(b,'/dashboard',cash='N$ 2,000.00',principal_disbursed='N$ 0.00',owed='N$ 0.00')
        assert dump(root)==before
