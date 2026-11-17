from types import SimpleNamespace
from moneyhub.identity import owner_name,lender_label
from moneyhub.receipts import whatsapp_message,pdf_content


def test_neutral_identity_is_locally_configurable(monkeypatch):
    monkeypatch.delenv('MYMONEYHUB_DISPLAY_NAME',raising=False)
    assert owner_name()=='Local Lender'
    monkeypatch.setenv('MYMONEYHUB_DISPLAY_NAME','Fictional Demo Lender')
    assert lender_label()=='Fictional Demo Lender — OSI LOAN'
    contract=SimpleNamespace(id=1,loan_id=1,person='Demo Borrower',direction='lend',
        principal=100000,interest=20000,rate_bps=2000,basis='fixed',start='2026-01-01',due='2026-02-01',purpose='Fictional study costs')
    assert 'Fictional Demo Lender' in whatsapp_message(contract,'Given Out',120000)
    assert ('Lender','Fictional Demo Lender — OSI LOAN') in pdf_content(contract,'Given Out',payout_entry=SimpleNamespace(date='2026-01-01'),remaining=120000)['fields']
