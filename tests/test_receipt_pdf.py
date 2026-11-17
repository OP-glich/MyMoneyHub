import os
from pathlib import Path
import re
import struct
import zlib

import pytest

from moneyhub.demo import seed_demo
from moneyhub.web import create_app
from moneyhub.pdf_export import make_pdf


def pdf_text(content):
    """Independently decode embedded PDF Unicode mappings and text commands."""
    objects = {int(number):body for number,body in re.findall(rb'(\d+) 0 obj\n(.*?)\nendobj', content, re.S)}
    streams = {}
    for ident,body in objects.items():
        match = re.search(rb'stream\n(.*?)\nendstream', body, re.S)
        if match:
            streams[ident] = zlib.decompress(match[1])
    fonts = {}
    for ident,body in objects.items():
        match = re.search(rb'/ToUnicode (\d+) 0 R', body)
        if match:
            mapping = streams[int(match[1])]
            pairs = {}
            for block in re.findall(rb'beginbfchar\n(.*?)\nendbfchar', mapping, re.S):
                for cid,unicode in re.findall(rb'<([0-9A-F]+)> <([0-9A-F]+)>', block):
                    pairs[int(cid,16)] = bytes.fromhex(unicode.decode()).decode('utf-16-be')
            fonts[ident] = pairs
    text = []
    for body in objects.values():
        if re.search(rb'/Type /Page\b', body):
            aliases = {int(index):fonts[int(ident)] for index,ident in re.findall(rb'/F(\d+) (\d+) 0 R', body)}
            stream = streams[int(re.search(rb'/Contents (\d+) 0 R', body)[1])]
            for index,encoded in re.findall(rb'/F(\d+) [^\n]*?<([0-9A-F]*)> Tj', stream):
                text.append(''.join(aliases[int(index)][int(encoded[i:i+4],16)] for i in range(0,len(encoded),4)))
    return '\n'.join(text)


@pytest.mark.skipif(not (Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'segoeui.ttf').is_file(), reason='PDF exporter uses installed Windows fonts')
def test_real_saved_loan_and_repayment_pdf(tmp_path, monkeypatch):
    app = create_app(tmp_path/'pdf.db', testing=True, demo=True)
    try:
        seed_demo(app.extensions['engine'])
        c = app.test_client()
        c.get('/login')
        with c.session_transaction() as sess: csrf = sess['csrf']
        c.post('/login',data={'csrf':csrf,'password':'ExploreMyMoney!2026'})
        loan = c.get('/loans/1/receipt?pdf=1')
        assert loan.status_code == 200 and loan.mimetype == 'application/pdf'
        assert loan.data.startswith(b'%PDF-1.7') and loan.data.rstrip().endswith(b'%%EOF')
        text = pdf_text(loan.data)
        for expected in ['OSI LOAN','PERSONAL LOAN ACKNOWLEDGEMENT','Demo Borrower A','School supplies',
                         'N$ 2,500.00','5% fixed for the loan term','N$ 125.00','N$ 2,625.00','Local Lender — OSI LOAN','Given Out — Awaiting Repayment']:
            assert expected in text
        payment = c.get('/repayments/1/receipt?pdf=1')
        assert payment.status_code == 200 and payment.mimetype == 'application/pdf'
        text = pdf_text(payment.data)
        for expected in ['REPAYMENT RECEIPT','Amount received','N$ 625.00','Remaining loan balance','N$ 2,000.00','DEMO-001']:
            assert expected in text
        def fail(_): raise ValueError('Use browser print: local font unavailable.')
        monkeypatch.setattr('moneyhub.pdf_export.make_pdf', fail)
        failed=c.get('/loans/1/receipt.pdf')
        assert failed.status_code==422 and failed.mimetype=='text/plain'
        assert b'local font unavailable' in failed.data
        assert 'Content-Disposition' not in failed.headers
    finally:
        app.extensions['engine'].dispose()


def test_pdf_wraps_long_values_and_preserves_unicode():
    font = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'segoeui.ttf'
    if not font.is_file(): pytest.skip('Windows fonts required')
    data = dict(reference='OSI-1234',subtitle='PERSONAL LOAN ACKNOWLEDGEMENT',
                borrower='João Amutenya',purpose='School supplies and transport. '*18,
                fields=[('Lender','Local Lender — OSI LOAN'),('Purpose','A'*500)],
                status='Pending — no money paid out',total_label='Total amount to repay',
                total='N$ 1,200.00',total_note='Principal + interest',note='Payout pending.')
    content = make_pdf(data)
    text = pdf_text(content)
    assert 'João Amutenya' in text and 'N$ 1,200.00' in text and 'Payout pending.' in text
    assert len(re.findall(rb'/Type /Page\b',content)) >= 2
