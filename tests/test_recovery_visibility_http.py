"""Saved drafts must not look lost when Given Out has no records."""
from datetime import date,timedelta
from http_workflows import live_app
from test_acceptance_http import saved,text,get_contract,dump,read_db


def test_original_drafts_visible_by_default_without_changing_financial_history(tmp_path):
    root=tmp_path/'visibility'
    with live_app(root) as b:
        b.signup()
        ids=[]
        due=(date.today()+timedelta(days=30)).isoformat()
        for person,amount in [('Original draft','300'),('Newer draft','400')]:
            page=saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person=person,amount=amount,rate='20',due=due)))
            ids.append(get_contract(page))
        before=dump(root)
        page=b.page('/loans')
        assert 'Pending (2)' in text(page)
        for ident in ids: assert f'id="loan-{ident}"' in page.html
        assert 'Download PDF Receipt' not in text(page)
        assert 'Confirm Payout' not in text(page)
        assert 'Generate Loan & Receipt' in text(page)
        assert dump(root)==before
        assert read_db(root,'SELECT COUNT(*) FROM entries')==[(0,)]
        saved(b.submit(b.page('/settings'),'/action/opening',dict(amount='2000')))
        issued=saved(b.submit(b.page('/loans'),'/action/loan_plan',dict(person='New valid issued loan',amount='1000',rate='20',due=due),submitter='/action/issue_loan'))
        issued_id=get_contract(issued)
        page=b.page('/loans')
        assert f'id="loan-{issued_id}"' in page.html
        assert 'Pending (2)' in text(page) and 'Given Out (1)' in text(page)
        assert 'Download PDF Receipt' in text(page)
        pending=b.page('/loans?status=pending')
        for ident in ids: assert f'id="loan-{ident}"' in pending.html
        final=dump(root)
    with live_app(root) as b:
        b.signin()
        assert dump(root)==final
        assert 'Given Out (1)' in text(b.page('/loans'))
        assert 'Original draft' in text(b.page('/loans?status=pending'))
