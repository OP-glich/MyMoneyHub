from datetime import date,timedelta
from pathlib import Path
from http_workflows import live_app,Page
from test_acceptance_http import saved,text,card,get_contract,metric,read_db,dump
from test_receipt_pdf import pdf_text


def test_visible_repayment_full_payment_register_and_offline_reports(tmp_path):
    root=tmp_path/'lending'
    with live_app(root,launcher=True) as b:
        b.signup()
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='2000')))
        due=(date.today()+timedelta(days=10)).isoformat()
        loans=saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person='Lending report borrower',purpose='School fees',amount='1000',rate='20',due=due),submitter='/action/issue_loan'))
        ident=get_contract(loans)
        loan=card(loans,ident)
        assert '<summary class="button repayment-trigger">Record Repayment</summary>' in loan.html
        assert loan.html.index('Record Repayment')<loan.html.index('Download PDF Receipt')
        assert '<summary>More Actions</summary>' in loan.html
        metric(b,'/dashboard',cash='N$ 1,000.00',owed='N$ 1,200.00')
        records=b.page('/loans/records?q=OSI-'+str(ident).zfill(4))
        assert 'Lending report borrower' in text(records) and 'N$ 1,200.00' in text(records)
        owed,headers=b.download('/loans/reports/owed.pdf')
        assert headers['Content-Type']=='application/pdf'
        assert 'attachment;' in headers['Content-Disposition'] and '.pdf' in headers['Content-Disposition']
        assert 'Lending report borrower' in pdf_text(owed)
        assert 'Total outstanding: N$ 1,200.00' in pdf_text(owed)
        loan_id=loan.values(loan.find_form('/action/repayment'))['loan_id']
        loans=saved(b.submit(loans,'/action/repayment',dict(amount='500',reference='REPORT-PART'),matching=dict(loan_id=loan_id)))
        assert card(loans,ident).metric('outstanding')=='N$ 700.00'
        assert 'Lending report borrower' in text(b.page('/loans/records?status=partial'))
        metric(b,'/dashboard',cash='N$ 1,500.00',owed='N$ 700.00',received='N$ 200.00')
        assert 'already recorded' in text(b.repeat())
        assert 'Pay Full Remaining Amount' in text(card(loans,ident))
        full=b.page(f'/loans/{ident}/repayment?full=1')
        assert full.values(full.find_form('/action/repayment'))['amount']=='700.00'
        assert 'Lending report borrower' in text(full) and 'N$ 700.00' in text(full)
        final=saved(b.submit(full,'/action/repayment',dict(reference='REPORT-FINAL')))
        assert card(final,ident).metric('status')=='Repaid With Interest'
        metric(b,'/loans',money_repaid='N$ 1,200.00',interest_collected='N$ 200.00',money_still_owed='N$ 0.00')
        assert 'Lending report borrower' in text(b.page('/loans/records?status=repaid'))
        before=dump(root)
        reports={}
        for kind in ['owed','repaid','history']:
            pdf,headers=b.download('/loans/reports/'+kind+'.pdf')
            assert headers['Content-Type']=='application/pdf'
            assert pdf.startswith(b'%PDF-1.7') and pdf.rstrip().endswith(b'%%EOF')
            reports[kind]=pdf_text(pdf)
            (root/(kind+'.pdf')).write_bytes(pdf)
        assert 'Lending report borrower' not in reports['owed']
        assert 'No matching loan records.' in reports['owed']
        for kind in ['repaid','history']:
            assert 'Lending report borrower' in reports[kind]
            assert 'Interest collected: N$ 200.00' in reports[kind]
        assert 'Principal given out: N$ 1,000.00' in reports['history']
        assert 'Repayment REPORT-PART: N$ 500.00' in reports['history']
        assert 'Repayment REPORT-FINAL: N$ 700.00' in reports['history']
        assert date.today().isoformat() in reports['history']
        original=b.download(f'/loans/{ident}/receipt.pdf')[0]
        assert 'OSI-'+str(ident).zfill(4) in pdf_text(original)
        for payment,remaining in [(1,'N$ 700.00'),(2,'N$ 0.00')]:
            pdf=b.download(f'/repayments/{payment}/receipt.pdf')[0]
            assert remaining in pdf_text(pdf)
        assert dump(root)==before
    with live_app(root,launcher=True) as b:
        b.signin()
        assert dump(root)==before
        assert b.download(f'/loans/{ident}/receipt.pdf')[0]==original
        assert 'Lending report borrower' in text(b.page('/loans/records?status=repaid'))


def test_records_search_sort_pending_overdue_and_date_filters(tmp_path):
    root=tmp_path/'filters'
    with live_app(root) as b:
        b.signup()
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='2000')))
        today=date.today()
        old=(today-timedelta(days=10)).isoformat()
        past=(today-timedelta(days=1)).isoformat()
        saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person='Zed overdue',amount='100',rate='10',date=old,due=past),submitter='/action/issue_loan'))
        saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person='Example pending',amount='300',rate='20',due=(today+timedelta(days=20)).isoformat())))
        allrows=b.page('/loans/records')
        assert 'Zed overdue' in text(allrows) and 'Example pending' in text(allrows)
        assert 'Pending agreements' in text(allrows) and 'Overdue' in text(allrows)
        assert 'Example pending' not in text(b.page('/loans/records?status=overdue'))
        assert 'Zed overdue' not in text(b.page('/loans/records?q=Anna'))
        owed=pdf_text(b.download('/loans/reports/owed.pdf')[0])
        assert 'Zed overdue' in owed and 'Example pending' not in owed
        assert 'Total outstanding: N$ 110.00' in owed
        for sort in ['date','name','amount','due']:
            for order in ['asc','desc']:
                assert b.page(f'/loans/records?sort={sort}&order={order}')
        filtered=pdf_text(b.download('/loans/reports/history.pdf?from='+today.isoformat())[0])
        assert 'Example pending' in filtered and 'Zed overdue' not in filtered
        assert 'No verified payout' in filtered
        before=dump(root)
        invalid=b.submit(b.page('/loans'),'/action/loan_plan',dict(person='Rejected future payout',amount='3000',rate='20',due=(today+timedelta(days=20)).isoformat()),submitter='/action/issue_loan')
        assert b.requests[-1][2]==422
        assert dump(root)==before


def test_register_orders_use_saved_values_and_pdf_paginates():
    from types import SimpleNamespace as Obj
    from moneyhub.loan_records import register,report_pdf
    from moneyhub.loan_states import LoanState
    contracts=[Obj(id=1,loan_id=None,person='Zed',principal=10000,interest=1000,start='2026-01-01',due='2026-03-01',direction='lend'),
               Obj(id=2,loan_id=None,person='Anna',principal=20000,interest=2000,start='2026-01-02',due='2026-02-01',direction='lend')]
    data=dict(contracts=contracts,loan_states={c.id:LoanState('pending','Pending Payout',False) for c in contracts},payments=[],loan_balances={},entry_map={},loan_map={})
    for sort,expected in [('name',[2,1]),('date',[1,2]),('amount',[1,2]),('due',[2,1])]:
        assert [r['contract'].id for r in register(data,{'sort':sort,'order':'asc'})['rows']]==expected
        assert [r['contract'].id for r in register(data,{'sort':sort,'order':'desc'})['rows']]==list(reversed(expected))
    many=register(data,{})['rows']*15
    pdf=report_pdf('history',many,{})
    content=pdf_text(pdf)
    assert content.count('No cash movements recorded.')==30
    assert content.count('OSI-0001')==15 and content.count('OSI-0002')==15
    assert pdf.count(b'/Type /Page ')>1
