import re
from datetime import date,timedelta
from urllib.error import HTTPError
import pytest
from http_workflows import live_app
from test_acceptance_http import saved,card,get_contract,read_db,metric,text


def test_receipt_sort_delete_confirmation_and_restart(tmp_path):
    root=tmp_path/'receipts'
    with live_app(root) as b:
        b.signup()
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='2000')))
        ids=[]
        for i,amount in enumerate(['100','200']):
            page=saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person='Receipt '+str(i),amount=amount,
                rate='20',date=(date.today()-timedelta(days=2-i)).isoformat(),due=(date.today()+timedelta(days=30)).isoformat()),submitter='/action/issue_loan'))
            ids.append(get_contract(page))
        for sort in ['date','serial','amount']:
            for order,expected in [('asc',ids),('desc',list(reversed(ids)))]:
                page=b.page(f'/loans?sort={sort}&order={order}')
                assert [int(n) for n in re.findall(r'<article id="loan-(\d+)"',page.html)]==expected
        ident=ids[0]
        page=b.page('/loans')
        loan=card(page,ident)
        assert f'/receipts/loan/{ident}/delete' in [a.get('href') for a in loan.links]
        loan_id=loan.values(loan.find_form('/action/repayment'))['loan_id']
        saved(b.submit(page,'/action/repayment',dict(amount='20',reference='RECEIPT-DELETE-TEST'),matching=dict(loan_id=loan_id)))
        cash=read_db(root,'SELECT SUM(amount) FROM entries')
        before=read_db(root,'SELECT * FROM entries')
        confirm=b.page(f'/receipts/loan/{ident}/delete')
        assert 'Delete receipt?' in text(confirm)
        assert read_db(root,'SELECT COUNT(*) FROM receipt_deletions')==[(0,)]
        # Cancel/GET does nothing; an explicit confirmation is mandatory server-side.
        invalid=b.submit(confirm,'/action/receipt_delete',dict(confirm='no'))
        assert b.requests[-1][2]==422 and 'Confirm receipt deletion' in text(invalid)
        saved(b.submit(b.page(f'/receipts/loan/{ident}/delete'),'/action/receipt_delete'))
        assert b.requests[-1][2]==200
        deleted_card=card(b.page('/loans'),ident)
        assert 'Receipt deleted' in text(deleted_card)
        assert f'/loans/{ident}/receipt.pdf' not in [a.get('href') for a in deleted_card.links]
        assert 'Record Repayment' in text(deleted_card)
        for suffix in ['', '.pdf','.txt','?pdf=1','?format=txt']:
            with pytest.raises(HTTPError) as error: b.get(f'/loans/{ident}/receipt'+suffix)
            assert error.value.code==410
        assert b.download(f'/loans/{ids[1]}/receipt.pdf')[0].startswith(b'%PDF-')
        payment=read_db(root,'SELECT id FROM repayments')[0][0]
        saved(b.submit(b.page(f'/receipts/repayment/{payment}/delete'),'/action/receipt_delete'))
        for suffix in ['', '.pdf','.txt']:
            with pytest.raises(HTTPError) as error: b.get(f'/repayments/{payment}/receipt'+suffix)
            assert error.value.code==410
        assert read_db(root,'SELECT * FROM entries')==before
        assert read_db(root,'SELECT SUM(amount) FROM entries')==cash
        assert read_db(root,'SELECT COUNT(*) FROM repayments')==[(1,)]
    with live_app(root) as b:
        b.signin()
        assert read_db(root,'SELECT COUNT(*) FROM receipt_deletions')==[(2,)]
        assert read_db(root,'SELECT * FROM entries')==before
        assert 'Receipt deleted' in text(card(b.page('/loans'),ident))
