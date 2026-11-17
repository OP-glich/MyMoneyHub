"""Issued receipts and cash are one user action; drafts remain separate."""
from datetime import date, timedelta

from http_workflows import live_app
from test_acceptance_http import saved, text, metric, card, get_contract, read_db, dump
from test_receipt_pdf import pdf_text
from test_everyday import ui, post
from moneyhub.db import read
from moneyhub import finance as f
from moneyhub.models import Loan, LoanContract, Entry
from sqlalchemy import select


def test_generate_loan_receipt_from_visible_form_and_restart(tmp_path):
    root=tmp_path/'issued'
    with live_app(root,launcher=True) as b:
        b.signup()
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='2000')))
        page=b.page('/loans?add=loan')
        assert [a['href'].split('&')[0] for a in page.links if a.get('href','').startswith('/loans?status=')]==[
            '/loans?status=pending','/loans?status=given_out','/loans?status=repaid']
        assert 'Confirm Payout' not in text(page) and 'Mark as Paid Out' not in text(page)
        values=dict(person='Receipt workflow borrower',purpose='Study supplies',amount='1000',rate='20',
                    due=(date.today()+timedelta(days=30)).isoformat())
        page=saved(b.submit(page,'/action/loan_plan',values,submitter='/action/issue_loan'))
        issued_post=b.last_post
        ident=get_contract(page)
        loan=card(page,ident)
        assert loan.metric('status')=='Given Out — Awaiting Repayment'
        assert loan.metric('expected')=='N$ 1,200.00'
        assert {'Download PDF Receipt','View Receipt','Copy WhatsApp Message','Record Repayment'}<=set(loan.text)
        assert not any(f['action']=='/action/payout' for f in loan.forms)
        assert f'/loans/{ident}/receipt.pdf' in [a.get('href') for a in loan.links]
        metric(b,'/loans',total_given_out='N$ 1,000.00',money_still_owed='N$ 1,200.00',money_repaid='N$ 0.00',interest_collected='N$ 0.00')
        metric(b,'/dashboard',cash='N$ 1,000.00',owed='N$ 1,200.00')
        pdf,headers=b.download(f'/loans/{ident}/receipt.pdf')
        assert headers['Content-Type']=='application/pdf'
        assert f'OSI-{ident:04d}.pdf' in headers['Content-Disposition']
        assert pdf.startswith(b'%PDF-1.7') and pdf.rstrip().endswith(b'%%EOF')
        assert 'Receipt workflow borrower' in pdf_text(pdf) and 'N$ 1,200.00' in pdf_text(pdf)
        assert 'N$ 200.00' in loan.textareas[f'loan-message-{ident}']
        (root/'issued-receipt.pdf').write_bytes(pdf)
        before=dump(root)
        b.last_post=issued_post
        assert b.repeat() and b.requests[-1][2]==422
        assert dump(root)==before
        loan_id=loan.values(loan.find_form('/action/repayment'))['loan_id']
        saved(b.submit(b.page('/loans'),'/action/repayment',dict(amount='500',reference='ISSUED-PART'),matching=dict(loan_id=loan_id)))
        assert card(b.page('/loans'),ident).metric('outstanding')=='N$ 700.00'
        saved(b.submit(b.page('/loans'),'/action/repayment',dict(amount='700',reference='ISSUED-FINAL'),matching=dict(loan_id=loan_id)))
        repaid=b.page('/loans?status=repaid')
        assert card(repaid,ident).metric('status')=='Repaid With Interest'
        assert f'id="loan-{ident}"' not in b.page('/loans?status=given_out').html
        metric(b,'/loans',money_repaid='N$ 1,200.00',interest_collected='N$ 200.00',money_still_owed='N$ 0.00')
        metric(b,'/dashboard',cash='N$ 2,200.00')
        final=dump(root)
        final_pdf=b.download(f'/loans/{ident}/receipt.pdf')[0]
        assert dump(root)==final
        assert read_db(root,"SELECT COUNT(*) FROM entries WHERE kind='loan'")==[(1,)]
    with live_app(root,launcher=True) as b:
        b.signin()
        assert dump(root)==final
        assert b.download(f'/loans/{ident}/receipt.pdf')[0]==final_pdf
        assert card(b.page('/loans?status=repaid'),ident).metric('outstanding')=='N$ 0.00'


def test_receipt_failure_rolls_back_agreement_payout_and_cash(ui,monkeypatch):
    app,client=ui
    post(client,'opening','receipt-opening',amount='2000',date=date.today().isoformat())
    def fail(_):
        raise ValueError('PDF preflight failure')
    monkeypatch.setattr('moneyhub.pdf_export.make_pdf',fail)
    response=post(client,'issue_loan','failed-issue',section='loans',person='Must not be issued',
                  amount='1000',rate='20',date=date.today().isoformat(),
                  due=(date.today()+timedelta(days=30)).isoformat())
    assert response.status_code==422
    assert b'Nothing was issued or deducted' in response.data
    with read(app.extensions['engine']) as s:
        assert list(s.scalars(select(Loan)))==[]
        assert list(s.scalars(select(LoanContract)))==[]
        assert sum(e.amount for e in s.scalars(select(Entry)))==200000
