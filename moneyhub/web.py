import csv
from decimal import Decimal
import secrets
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

from flask import (Flask, render_template, request, session, redirect, url_for,
                   flash, abort, Response, send_file, jsonify)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import SecurityError

from .db import data_dir, engine_for, read, write, backup
from .models import (Owner, Account, Category, Entry, Budget, Saving, Loan,
                     Repayment, Product, Lot, Sale, Preference, MonthlyBudget, LoanContract, ReceiptDeletion)
from . import finance as f
from .receipts import reference, interest_label, whatsapp_message, pdf_content
from .loan_states import filter_code
from .identity import owner_name,lender_label


def create_app(database=None, testing=False, demo=False):
    app = Flask(__name__)
    root = Path(database).parent if database else data_dir(demo)
    root.mkdir(parents=True, exist_ok=True)
    database = Path(database) if database else root / 'moneyhub.db'
    secret_file = root / 'session.secret'
    try:
        with secret_file.open('x', encoding='utf-8') as file:
            file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    app.config.update(SECRET_KEY=secret_file.read_text(encoding='utf-8'), TESTING=testing,
                      MAX_CONTENT_LENGTH=2*1024*1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                      TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    engine = engine_for(database)
    app.extensions['engine'] = engine
    app.extensions['database'] = database
    attempts = []

    @app.before_request
    def protect():
        if request.host.split(':')[0] not in ('localhost', '127.0.0.1'):
            abort(400)
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        if request.method == 'POST':
            token = request.form.get('csrf', '')
            if not token.isascii() or not secrets.compare_digest(token, session['csrf']):
                abort(400, 'This form has expired. Reload the page and sign in again if needed. Nothing was saved.')
        if request.endpoint not in ('login', 'static') and not session.get('owner'):
            return redirect(url_for('login'))

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        return response

    app.jinja_env.filters['nad'] = f.nad
    app.jinja_env.filters['decimal_rate'] = lambda value: format(Decimal(value)/100, '.2f')
    app.jinja_env.filters['cents_input'] = lambda value: format(Decimal(value)/100, '.2f')
    app.jinja_env.filters['loan_ref'] = reference
    app.jinja_env.filters['interest_label'] = interest_label

    @app.context_processor
    def common():
        return dict(receipt_owner_name=owner_name(),receipt_lender_label=lender_label(),csrf=session.get('csrf'), today=date.today().isoformat(),
                    month=date.today().strftime('%Y-%m'), demo=demo,
                    nonce=lambda: secrets.token_hex(20),
                    form_value=lambda action, field, default='', kind=None: default)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        with Session(engine) as s:
            owner = s.get(Owner, 1)
        setup = owner is None
        if request.method == 'POST':
            now = time.monotonic()
            attempts[:] = [t for t in attempts if now-t < 300]
            if len(attempts) >= 10:
                flash('Too many attempts. Wait five minutes.', 'error')
                return render_template('login.html', setup=setup), 429
            password = request.form.get('password', '')
            if setup:
                if len(password) < 12 or len(password) > 256 or password != request.form.get('confirm'):
                    flash('Use 12–256 characters and matching passwords.', 'error')
                    return render_template('login.html', setup=True), 400
                try:
                    with write(engine) as s:
                        if s.get(Owner, 1):
                            raise ValueError('Owner already exists. Sign in.')
                        s.add(Owner(id=1, password_hash=generate_password_hash(password)))
                        for label, kind in [('Salary', 'income'), ('Other income', 'income'),
                                            ('Food', 'expense'), ('Transport', 'expense'),
                                            ('Gifts', 'income'), ('Shopping', 'expense'),
                                            ('NSFAF', 'income'), ('Side Hustle', 'income'),
                                            ('Housing', 'expense'), ('Other expenses', 'expense')]:
                            s.add(Category(name=label, kind=kind))
                except (ValueError, IntegrityError):
                    flash('Owner already exists. Sign in.', 'error')
                    return redirect(url_for('login'))
            elif len(password) > 256 or not check_password_hash(owner.password_hash, password):
                attempts.append(now)
                flash('Incorrect password.', 'error')
                return render_template('login.html', setup=False), 401
            session.clear()
            session['owner'] = 1
            session['csrf'] = secrets.token_hex(32)
            session.permanent = True
            attempts.clear()
            return redirect(url_for('page', section='dashboard'))
        return render_template('login.html', setup=setup)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    def filtered_entries(s):
        query = select(Entry).order_by(Entry.date.desc(), Entry.id.desc())
        if request.args.get('q'):
            query = query.where(Entry.note.contains(request.args['q'], autoescape=True))
        if request.args.get('account'):
            query = query.where(Entry.account_id == f.integer(request.args['account']))
        if request.args.get('category'):
            query = query.where(Entry.category_id == f.integer(request.args['category']))
        if request.args.get('kind'):
            query = query.where(Entry.kind == request.args['kind'])
        for parameter, comparator in [('from', Entry.date.__ge__), ('to', Entry.date.__le__)]:
            if request.args.get(parameter):
                query = query.where(comparator(f.day(request.args[parameter])))
        return list(s.scalars(query))

    @app.get('/')
    def home():
        return redirect(url_for('page', section='dashboard'))

    @app.get('/<section>')
    def page(section):
        section = {'sales': 'inventory', 'overview': 'dashboard'}.get(section, section)
        if section not in ('dashboard', 'accounts', 'transactions', 'budgets', 'savings', 'loans', 'inventory', 'reports', 'settings'):
            abort(404)
        return render_page(section)

    @app.get('/loans/records')
    @app.get('/loans/reports/<kind>.pdf')
    def loan_records(kind=None):
        from .summaries import snapshot
        from .loan_records import register,report_pdf,TITLES
        if kind is not None and kind not in TITLES:
            abort(404)
        with read(engine) as s:
            data=snapshot(s,date.today().strftime('%Y-%m'))
            try:
                view=register(data,request.args)
            except ValueError as exc:
                return render_template('message.html',section='loans',message=str(exc)),422
            if kind:
                # Main reports always select their own financial population;
                # search and issue-date range remain applicable.
                args=request.args.to_dict();args['status']='all'
                rows=[r for r in register(data,args)['rows'] if r['contract'].direction=='lend']
                if kind=='owed':
                    rows=[r for r in rows if r['state'].payout_verified and r['outstanding']>0]
                elif kind=='repaid':
                    rows=[r for r in rows if r['state'].code=='repaid']
                try:
                    content=report_pdf(kind,rows,data['entry_map'],view['from_date'],view['to_date'])
                except (ValueError,OSError) as exc:
                    return Response('PDF unavailable: '+str(exc),status=422,content_type='text/plain; charset=utf-8')
                return Response(content,mimetype='application/pdf',headers={'Content-Disposition':f'attachment; filename="OSI-LOAN-{kind}-{date.today().isoformat()}.pdf"'})
            return render_template('loan_records.html',section='loans',**view)

    @app.get('/loans/<int:contract_id>/repayment')
    def repayment_form(contract_id):
        from .summaries import snapshot
        from .loan_records import register
        with read(engine) as s:
            data=snapshot(s,date.today().strftime('%Y-%m'))
            row=next((r for r in register(data,{})['rows'] if r['contract'].id==contract_id),None)
            if row is None:
                abort(404)
            if not row['state'].payout_verified or row['outstanding']<=0:
                return render_template('message.html',section='loans',message='Only an issued loan with money outstanding can receive a repayment.'),409
            return render_template('repayment_form.html',section='loans',r=row,
                                   full=request.args.get('full')=='1')

    def render_page(section, failed_action=None, failed_form=None, quote=None, status=200, edit_id=None):
        from .summaries import snapshot
        with read(engine) as s:
            chosen_month = request.args.get('month', date.today().strftime('%Y-%m')) if section != 'dashboard' else date.today().strftime('%Y-%m')
            if failed_form and failed_form.get('month'):
                try:
                    f.day(failed_form['month']+'-01')
                    chosen_month = failed_form['month']
                except ValueError:
                    pass
            try:
                data = snapshot(s, chosen_month)
                data['filtered'] = filtered_entries(s)
                deleted=list(s.scalars(select(ReceiptDeletion)))
                data['deleted_loans']={r.contract_id for r in deleted if r.contract_id}
                data['deleted_payments']={r.repayment_id for r in deleted if r.repayment_id}
                sort=request.args.get('sort','date')
                sort=sort if sort in ('date','serial','amount') else 'date'
                order='asc' if request.args.get('order')=='asc' else 'desc'
                data['receipt_sort'],data['receipt_order']=sort,order
                def loan_key(c):
                    return (c.id if sort=='serial' else c.principal+c.interest if sort=='amount' else
                            data['entry_map'][data['loan_map'][c.loan_id].entry_id].date if c.loan_id else c.start,c.id)
                data['contracts']=sorted(data['contracts'],key=loan_key,reverse=order=='desc')
                data['payments']=sorted(data['payments'],key=lambda p:(p.id if sort=='serial' else p.principal+p.interest if sort=='amount' else data['entry_map'][p.entry_id].date,p.id),reverse=order=='desc')
                edit_entry = f.editable_entry(s, edit_id) if edit_id else None
            except ValueError as exc:
                flash(str(exc), 'error')
                return render_template('message.html', message=str(exc), section=section), 400
            def form_value(action, field, default='', kind=None):
                action_name, _, record = (action or '').partition(':')
                record_field = {'sale':'product_id','purchase':'product_id','payout':'contract_id','repayment':'loan_id'}.get(action_name,'id')
                same_record = not record or str((failed_form or {}).get(record_field)) == record
                if failed_action == action_name and same_record and (action_name != 'transaction' or kind is None or (failed_form or {}).get('kind') == kind):
                    return (failed_form or {}).get(field, default)
                return default
            failed_contract = (failed_form or {}).get('contract_id')
            selected_loan=request.args.get('saved', type=int)
            if failed_contract:
                try:
                    selected_loan=int(failed_contract)
                except (TypeError,ValueError):
                    pass
            selected_status=filter_code(request.args.get('status'))
            if selected_status in ('paid_out','overdue'):
                selected_status='given_out'
            if selected_status not in ('pending','given_out','repaid'):
                # Do not open an empty Given Out tab while saved drafts exist.
                states=list(data['loan_states'].values())
                selected_status=('given_out' if any(v.section=='given_out' for v in states) else
                                 'pending' if any(v.section=='pending' for v in states) else
                                 'repaid' if any(v.section=='repaid' for v in states) else 'given_out')
            return render_template('hub.html', **data, section=section, chosen_month=chosen_month,
                                   failed_action=failed_action, failed_form=failed_form or {},
                                   form_value=form_value, quote=quote, edit_entry=edit_entry,
                                   selected_status=selected_status,
                                   selected_loan=selected_loan), status

    @app.post('/action/<action>')
    def action(action):
        form = request.form
        section = form.get('section', 'dashboard')
        if section == 'sales':
            section = 'inventory'
        if section not in ('accounts', 'transactions', 'budgets', 'savings', 'loans', 'inventory', 'settings'):
            section = 'dashboard'
        try:
            success = 'Saved successfully.'
            saved_loan = None
            with write(engine) as s:
                key = form.get('key', '')
                def aid():
                    if form.get('account_id'):
                        return f.get(s, Account, form['account_id']).id
                    if action == 'saving' and form.get('id'):
                        return f.get(s, Saving, form['id']).account_id
                    return f.default_account(s, create=True).id
                if action == 'account':
                    f.create_account(s, form.get('name'), form.get('amount'), form.get('date'), key)
                    success += ' A separate balance was added; existing balances were not combined.'
                elif action == 'opening':
                    f.opening_balance(s, form.get('amount'), form.get('date'), key)
                    success += ' Starting money was added to your personal balance.'
                elif action in ('entry_edit', 'entry_cancel'):
                    if action == 'entry_cancel' and form.get('confirm') != 'yes':
                        raise ValueError('Confirm cancellation first. The original record will stay in history.')
                    f.revise_entry(s, form.get('entry_id'), form.get('amount'), form.get('date'),
                                   form.get('category_id'), form.get('note'), key, cancel=action == 'entry_cancel')
                    success += ' Totals and budgets were recalculated. The original entry remains in history.'
                elif action == 'category':
                    f.operation(s, key, 'category')
                    kind = form.get('kind')
                    if kind not in ('income', 'expense'):
                        raise ValueError('Invalid category type.')
                    s.add(Category(name=f.name(form.get('name')), kind=kind))
                elif action == 'transaction':
                    if form.get('kind'):
                        f.simple_transaction(s, form['kind'], form.get('amount'), form.get('date'), form.get('note'), key,
                                             form.get('category_id') or None, form.get('account_id') or None)
                    else:
                        f.transaction(s, aid(), f.integer(form.get('category_id')), form.get('amount'), form.get('date'), form.get('note'), key)
                elif action == 'default_balance':
                    f.set_default(s, form.get('balance_id'), key)
                elif action == 'category_edit':
                    f.operation(s, key, 'category_edit')
                    f.get(s, Category, form.get('id')).name = f.name(form.get('name'))
                elif action == 'transfer':
                    f.transfer(s, aid(), f.integer(form.get('destination')), form.get('amount'), form.get('date'), key)
                elif action == 'budget':
                    f.budget(s, f.integer(form.get('category_id')), form.get('month', ''), form.get('amount'), key)
                elif action == 'monthly_budget':
                    f.monthly_budget(s, form.get('month', ''), form.get('amount'), key)
                elif action == 'saving':
                    f.saving(s, aid(), form.get('name'), form.get('amount'), form.get('target'), key, form.get('id') or None)
                elif action == 'loan':
                    contract = f.propose_loan(s, aid(), form.get('direction','lend'), form.get('person'),
                                              form.get('amount'), form.get('rate','0'), form.get('date'),
                                              form.get('due'), form.get('purpose'), key, terms=form.get('terms',''))
                    saved_loan = contract.id
                    success += ' '+reference(contract)+' saved as Pending Payout. No money has been paid out.'
                elif action in ('loan_plan', 'issue_loan'):
                    contract = f.propose_loan(s, aid(), form.get('direction', 'lend'), form.get('person'),
                                              form.get('amount'), form.get('rate', '0'), form.get('date'), form.get('due'),
                                              form.get('purpose'), key+'_agreement', basis=form.get('basis', 'fixed'),
                                              contact=form.get('contact', ''), terms=form.get('terms', ''))
                    saved_loan = contract.id
                    if action == 'issue_loan':
                        f.payout(s, contract.id, form.get('date'), key+'_payout', account_id=aid())
                        validate_issued_receipt(s, contract)
                        success += ' '+reference(contract)+' generated. Given Out — Awaiting Repayment. Principal recorded once; your receipt is ready.'
                    else:
                        success += ' '+reference(contract)+' saved as Pending draft. Cash is unchanged; no receipt has been issued.'
                elif action == 'payout':
                    f.payout(s, form.get('contract_id'), form.get('date'), key, account_id=aid())
                    saved_loan = f.integer(form.get('contract_id'))
                    validate_issued_receipt(s, s.get(LoanContract,saved_loan))
                    success += ' Given Out — Awaiting Repayment. Your loan and receipt have been issued together.'
                elif action == 'repayment':
                    f.repay(s, f.integer(form.get('loan_id')), aid(), form.get('amount'), form.get('date'),
                            form.get('reference') or ('Receipt-'+key), key,
                            auto_reference=not bool(form.get('reference')))
                    contract = s.scalar(select(LoanContract).where(LoanContract.loan_id == f.integer(form.get('loan_id'))))
                    saved_loan = contract.id if contract else None
                    success += ' Repayment recorded. Cash and the amount still owed were updated.'
                elif action == 'receipt_delete':
                    if form.get('confirm') != 'yes':
                        raise ValueError('Confirm receipt deletion first. Nothing was deleted.')
                    kind=form.get('receipt_kind')
                    if kind not in ('loan','repayment'):
                        raise ValueError('Unknown receipt type.')
                    row=f.get(s,LoanContract if kind=='loan' else Repayment,form.get('receipt_id'))
                    field=ReceiptDeletion.contract_id if kind=='loan' else ReceiptDeletion.repayment_id
                    if s.scalar(select(ReceiptDeletion).where(field==row.id)):
                        raise ValueError('This receipt has already been deleted.')
                    if kind=='loan' and not f.loan_state(s,row).payout_verified:
                        raise ValueError('This draft has no issued receipt to delete.')
                    f.operation(s,key,'receipt_delete')
                    s.add(ReceiptDeletion(**{('contract_id' if kind=='loan' else 'repayment_id'):row.id},deleted_at=datetime.now(timezone.utc).isoformat()))
                    contract=row if kind=='loan' else s.scalar(select(LoanContract).where(LoanContract.loan_id==row.loan_id))
                    saved_loan=contract.id if contract else None
                    success += ' Receipt deleted. Loan, repayment and cash history are unchanged.'
                elif action == 'item':
                    f.add_item(s, aid(), form.get('name'), form.get('quantity'), form.get('amount'),
                               form.get('selling_price'), form.get('date'), key)
                elif action == 'product':
                    f.operation(s, key, 'product')
                    s.add(Product(name=f.name(form.get('name'))))
                elif action in ('purchase', 'sale'):
                    method = f.purchase if action == 'purchase' else f.sell
                    method(s, f.integer(form.get('product_id')), aid(), form.get('quantity'), form.get('amount'), form.get('date'), key)
                elif action == 'import':
                    file = request.files.get('file')
                    if not file:
                        raise ValueError('Select a CSV file.')
                    try:
                        content = file.read().decode('utf-8-sig')
                    except UnicodeDecodeError:
                        raise ValueError('CSV must use UTF-8 encoding.') from None
                    count = f.import_csv(s, content)
                    flash(f'Imported {count} transactions.', 'success')
                elif action == 'password':
                    owner = s.get(Owner, 1)
                    password = form.get('password', '')
                    if not check_password_hash(owner.password_hash, form.get('current', '')):
                        raise ValueError('Current password is incorrect.')
                    if not 12 <= len(password) <= 256 or password != form.get('confirm'):
                        raise ValueError('Use 12–256 characters and matching passwords.')
                    owner.password_hash = generate_password_hash(password)
                else:
                    abort(404)
                saved_status = f.loan_state(s,s.get(LoanContract,saved_loan)).section if saved_loan else None
            flash(success, 'success')
        except (ValueError, IntegrityError, OperationalError, csv.Error) as exc:
            if isinstance(exc, IntegrityError):
                message = 'Duplicate name, import ID or payment reference, or invalid record. Nothing was saved.'
            elif isinstance(exc, OperationalError):
                message = 'Database is busy or unavailable. Nothing was saved; try again.'
            else:
                message = str(exc)
            flash(message, 'error')
            return render_page(section, failed_action='loan_plan' if action == 'issue_loan' else action, failed_form=form, status=422,
                               edit_id=form.get('entry_id') if action in ('entry_edit','entry_cancel') else None)
        arguments = {}
        if section == 'budgets' and form.get('month'):
            arguments['month'] = form['month']
        if saved_loan:
            arguments.update(saved=saved_loan, status=saved_status, _anchor='loan-'+str(saved_loan))
        return redirect(url_for('page', section=section, **arguments), code=303)

    def validate_issued_receipt(s, contract):
        """PDF failure rolls back the same transaction as the agreement and cash."""
        from .pdf_export import make_pdf
        loan = s.get(Loan,contract.loan_id)
        try:
            make_pdf(pdf_content(contract, f.loan_status(s,contract),
                                 payout_entry=s.get(Entry,loan.entry_id),
                                 remaining=sum(f.outstanding(s,loan))))
        except (ValueError,OSError) as exc:
            raise ValueError('Receipt could not be generated. Nothing was issued or deducted. '+str(exc)) from exc

    @app.get('/transactions/<int:entry_id>/edit')
    def entry_editor(entry_id):
        return render_page('transactions', edit_id=entry_id)

    @app.post('/loans/preview')
    def preview_form():
        values = request.form
        try:
            quote = calculate_quote(values)
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_page('loans', failed_action='loan_plan', failed_form=values, status=422)
        flash('Preview ready. Nothing has been saved or paid out yet.', 'success')
        return render_page('loans', failed_action='loan_plan', failed_form=values, quote=quote)

    @app.get('/export/ledger.csv')
    def export():
        with read(engine) as s:
            try:
                result = f.export_csv(s, filtered_entries(s))
            except ValueError as exc:
                return str(exc), 400
        return Response(result, mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=moneyhub-ledger.csv'})

    @app.get('/receipts/<kind>/<int:receipt_id>/delete')
    def confirm_receipt_delete(kind,receipt_id):
        if kind not in ('loan','repayment'):
            abort(404)
        with read(engine) as s:
            row=s.get(LoanContract if kind=='loan' else Repayment,receipt_id)
            if row is None:
                abort(404)
            field=ReceiptDeletion.contract_id if kind=='loan' else ReceiptDeletion.repayment_id
            if s.scalar(select(ReceiptDeletion.id).where(field==receipt_id)):
                abort(410)
            if kind=='loan' and not f.loan_state(s,row).payout_verified:
                abort(409)
            contract=row if kind=='loan' else s.scalar(select(LoanContract).where(LoanContract.loan_id==row.loan_id))
            return render_template('receipt_delete.html',section='loans',kind=kind,receipt_id=receipt_id,
                                   receipt_reference=reference(contract)+('-PAY-'+str(row.id) if kind=='repayment' else ''),person=contract.person)

    @app.get('/loans/<int:contract_id>/receipt')
    @app.get('/loans/<int:contract_id>/receipt.<format>')
    def loan_receipt(contract_id, format=None):
        if format not in (None, 'pdf', 'txt'):
            abort(404)
        with read(engine) as s:
            contract = s.get(LoanContract, contract_id)
            if contract is None:
                abort(404)
            if s.scalar(select(ReceiptDeletion.id).where(ReceiptDeletion.contract_id==contract_id)):
                abort(410,'This receipt has been deleted. Financial history is unchanged.')
            state = f.loan_state(s,contract)
            if not state.payout_verified:
                return render_template('loan_pending.html',contract=contract,state=state,section='loans'),409
            status = state.label
            payout_entry = s.get(Entry, s.get(Loan, contract.loan_id).entry_id) if contract.loan_id else None
            remaining = sum(f.outstanding(s, s.get(Loan, contract.loan_id))) if contract.loan_id else None
            message = whatsapp_message(contract, status, remaining)
            html = render_template('receipt.html', contract=contract, status=status,
                                   payout_entry=payout_entry, remaining=remaining, message=message,
                                   payment=None, state=state, section='loans')
            return receipt_response(html, reference(contract), pdf_content(contract, status, payout_entry=payout_entry, remaining=remaining),
                                    format=format, message=message)

    @app.get('/repayments/<int:payment_id>/receipt')
    @app.get('/repayments/<int:payment_id>/receipt.<format>')
    def repayment_receipt(payment_id, format=None):
        if format not in (None, 'pdf', 'txt'):
            abort(404)
        with read(engine) as s:
            payment = s.get(Repayment, payment_id)
            if payment is None:
                abort(404)
            if s.scalar(select(ReceiptDeletion.id).where(ReceiptDeletion.repayment_id==payment_id)):
                abort(410,'This receipt has been deleted. Financial history is unchanged.')
            contract = s.scalar(select(LoanContract).where(LoanContract.loan_id == payment.loan_id))
            if contract is None:
                abort(404)
            payment_entry = s.get(Entry, payment.entry_id)
            history = list(s.scalars(select(Repayment).where(Repayment.loan_id == payment.loan_id, Repayment.id <= payment.id)))
            remaining = contract.principal+contract.interest-sum(p.principal+p.interest for p in history)
            state=f.loan_state(s,contract)
            loan=s.get(Loan,payment.loan_id)
            if payment_entry.kind != 'repayment' or payment_entry.amount != ((payment.principal+payment.interest) * (1 if loan.direction=='lend' else -1)):
                return render_template('message.html',message='The saved repayment cash entry could not be verified. No records were changed.',section='loans'),409
            html = render_template('receipt.html', contract=contract, status=state.label,state=state,
                                   remaining=remaining, payment=payment, payment_entry=payment_entry,
                                   message=None, section='loans')
            return receipt_response(html, reference(contract)+'-PAY-'+str(payment.id),
                                    pdf_content(contract, f.loan_status(s, contract), remaining=remaining,
                                                payment=payment, payment_entry=payment_entry), format=format)

    def receipt_response(html, filename, data, format=None, message=None):
        format = format or ('pdf' if request.args.get('pdf') == '1' else request.args.get('format'))
        if format == 'txt':
            if not message:
                message = '\n'.join(['OSI LOAN', data['subtitle'], data['reference'],
                                     'Borrower: '+data['borrower'], 'Purpose: '+data['purpose'],
                                     'Status: '+data['status']] +
                                    [label+': '+value for label,value in data['fields']] +
                                    [data['total_label']+': '+data['total'], data['note']])
            return Response(message+'\n', content_type='text/plain; charset=utf-8', headers={
                'Content-Disposition': f'attachment; filename="{filename}.txt"'})
        if format != 'pdf':
            return html
        from .pdf_export import make_pdf
        try:
            content = make_pdf(data)
        except (ValueError,OSError) as exc:
            # Never disguise an HTML page as a successful PDF download.
            return Response('PDF unavailable: '+str(exc),status=422,content_type='text/plain; charset=utf-8')
        return Response(content, mimetype='application/pdf', headers={
            'Content-Disposition': f'attachment; filename="{filename}.pdf"'})

    @app.get('/loan-preview')
    def loan_preview():
        try:
            return jsonify(calculate_quote(request.args))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    def calculate_quote(values):
        principal = f.money(values.get('amount'), positive=True)
        rate = f.money(values.get('rate', '0'))
        start, due = f.day(values.get('date')), f.day(values.get('due'))
        days = (date.fromisoformat(due)-date.fromisoformat(start)).days
        if not 1 <= days <= 36500 or not 0 <= rate <= 100000:
            raise ValueError('Check the rate and repayment dates.')
        interest = f.loan_interest(principal, rate, values.get('basis', 'fixed'), days)
        if principal+interest > f.MAX_CENTS:
            raise ValueError('Total loan amount is too large.')
        return dict(interest=f.nad(interest), total=f.nad(principal+interest))

    @app.errorhandler(400)
    @app.errorhandler(413)
    def invalid_request(error):
        if isinstance(error, SecurityError):
            return Response('Invalid host. Open MyMoney Hub at 127.0.0.1.', status=400, mimetype='text/plain')
        message = 'That file is too large. Choose a CSV under 2 MB.' if error.code == 413 else str(error.description)
        return render_template('message.html', message=message, section='settings'), error.code

    @app.get('/export/import-template.csv')
    def import_template():
        return Response(','.join(f.CSV_FIELDS) + '\n', mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=import-template.csv'})

    @app.post('/backup')
    def download_backup():
        from datetime import datetime
        target = root / 'backups' / ('moneyhub-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
        backup(database, target)
        return send_file(target, as_attachment=True, download_name=target.name)

    return app
