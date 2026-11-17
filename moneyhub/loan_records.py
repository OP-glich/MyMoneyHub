"""Read-only lending register and offline reports, using the shared snapshot."""
from datetime import date
from decimal import Decimal
from . import finance as f
from .receipts import reference

TITLES={'owed':'People Who Owe Me','repaid':'People Who Repaid Me','history':'Complete Loan History'}


def register(data, args):
    query=str(args.get('q','')).strip()[:100]
    status=args.get('status','all')
    if status not in ('all','pending','given_out','partial','repaid','overdue'):
        status='all'
    sort=args.get('sort','date')
    if sort not in ('date','name','amount','due'):
        sort='date'
    order='asc' if args.get('order')=='asc' else 'desc'
    start=f.day(args['from']) if args.get('from') else ''
    end=f.day(args['to']) if args.get('to') else ''
    if start and end and start>end:
        raise ValueError('Start date must be on or before end date.')
    rows=[]
    for c in data['contracts']:
        state=data['loan_states'][c.id]
        payments=sorted((p for p in data['payments'] if p.loan_id==c.loan_id),key=lambda p:(data['entry_map'][p.entry_id].date,p.id)) if c.loan_id else []
        paid=sum(p.principal+p.interest for p in payments)
        remaining=sum(data['loan_balances'][c.loan_id]) if c.loan_id else 0
        issued=data['entry_map'][data['loan_map'][c.loan_id].entry_id].date if state.payout_verified else ''
        record_date=issued or c.start
        if query.casefold() not in (c.person+' '+reference(c)).casefold():
            continue
        if start and record_date<start or end and record_date>end:
            continue
        partial=state.payout_verified and 0<paid<c.principal+c.interest
        if status=='partial' and not partial or status not in ('all','partial') and status not in (state.section,state.code):
            continue
        rows.append(dict(contract=c,state=state,reference=reference(c),date=record_date,issued=issued,
                         principal=c.principal,interest=c.interest,expected=c.principal+c.interest,
                         paid=paid,outstanding=remaining if state.payout_verified or state.needs_review else 0,
                         interest_collected=sum(p.interest for p in payments),payments=payments,
                         final_date=data['entry_map'][payments[-1].entry_id].date if payments and state.code=='repaid' else '',
                         progress=Decimal(paid)*100/(c.principal+c.interest)))
    rows.sort(key=lambda r:(r['contract'].person.casefold() if sort=='name' else r['expected'] if sort=='amount' else r['contract'].due if sort=='due' else r['date'],r['contract'].id),reverse=order=='desc')
    return dict(rows=rows,query=query,record_status=status,record_sort=sort,record_order=order,from_date=start,to_date=end)


def report_pdf(kind, rows, entries, start='', end=''):
    from .pdf_export import PDF
    pdf=PDF()
    pdf.paragraph('OSI LOAN',26,True)
    pdf.paragraph(TITLES[kind],18,True)
    pdf.paragraph('Generated '+date.today().isoformat()+' | All amounts in NAD',9)
    pdf.paragraph('Loan issue/agreement dates: '+(start or 'Any')+' to '+(end or 'Any'),9)
    pdf.paragraph('Balances and payments reflect all saved activity as of generation. Pending agreements are not cash or receivables.',9)
    booked=[r for r in rows if r['state'].payout_verified]
    totals=[('Principal given out',sum(r['principal'] for r in booked)),
            ('Agreed interest on issued loans',sum(r['interest'] for r in booked)),
            ('Total repayable on issued loans',sum(r['expected'] for r in booked)),
            ('Money received',sum(r['paid'] for r in booked)),
            ('Interest collected',sum(r['interest_collected'] for r in booked)),
            ('Total outstanding',sum(r['outstanding'] for r in booked))]
    pdf.y+=12
    for label,amount in totals:
        pdf.paragraph(label+': '+f.nad(amount),11,True)
    pdf.paragraph('Pending agreements: '+str(sum(r['state'].code=='pending' for r in rows)),10)
    if not rows:
        pdf.paragraph('No matching loan records.',12)
    for r in rows:
        c=r['contract']
        pdf.space(145);pdf.y+=15
        pdf.paragraph(r['reference']+' | '+c.person,14,True)
        pdf.paragraph(r['state'].label+' | Due '+c.due,10,True)
        pdf.paragraph(('Issued '+r['issued']) if r['issued'] else 'Pending agreement '+c.start+' | No verified payout',10)
        for label,amount in [('Original principal',r['principal']),('Agreed interest',r['interest']),('Total repayable',r['expected']),('Amount received',r['paid']),('Amount outstanding',r['outstanding']),('Interest collected',r['interest_collected'])]:
            pdf.paragraph(label+': '+f.nad(amount),10)
        if r['final_date']:
            pdf.paragraph('Final repayment: '+r['final_date'],10)
        if kind=='history':
            pdf.paragraph('Financial history',10,True)
            events=[]
            if r['issued']:
                events.append((r['issued'],0,'Principal given out: '+f.nad(c.principal)))
            for p in r['payments']:
                events.append((entries[p.entry_id].date,p.id,'Repayment '+p.reference+': '+f.nad(p.principal+p.interest)+' | Interest '+f.nad(p.interest)+' | Principal '+f.nad(p.principal)))
            for when,_,message in sorted(events):
                pdf.paragraph(when+' | '+message,9)
            if not events:
                pdf.paragraph('No cash movements recorded.',9)
    return pdf.finish()
