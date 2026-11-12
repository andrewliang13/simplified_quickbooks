"""Customer invoices and received payments (A/R), vendor bills and bill payments (A/P).
The work is done by documents.py; this module keeps the routes and the helpers other modules use."""
from . import documents as docs, ledger
from .documents import PURCHASE, SALES, TERMS  # noqa: F401  (TERMS is used by /api/meta)
from .router import route


# ---------------------------------------------------------------- helpers used elsewhere

def get_invoice(conn, invoice_id, detail=True):
    return docs.get_doc(conn, SALES, invoice_id, detail)


def open_invoices(conn, customer_id=None):
    return docs.open_docs(conn, SALES, customer_id)


def open_bills(conn, vendor_id=None):
    return docs.open_docs(conn, PURCHASE, vendor_id)


def save_payment(conn, data, payment_id=None):
    """Sales payment from cents data: customer_id, amount_cents, deposit_account_id, applications."""
    apps = data.get('applications')
    return docs.save_pay(conn, SALES, {
        'name_id': data.get('customer_id'), 'date': data.get('date'), 'amount_cents': data['amount_cents'],
        'account_id': data.get('deposit_account_id'), 'memo': data.get('memo'), 'ref': data.get('ref'),
        'method': data.get('method'), 'exchange_rate': data.get('exchange_rate'),
        'applications': None if apps is None else [{'doc_id': a['invoice_id'], 'amount_cents': a['amount_cents']}
                                                   for a in apps]}, payment_id)


def save_bill_payment(conn, data):
    apps = data.get('applications')
    return docs.save_pay(conn, PURCHASE, {
        'name_id': data.get('vendor_id'), 'date': data.get('date'), 'amount_cents': data['amount_cents'],
        'account_id': data.get('pay_account_id'), 'memo': data.get('memo'),
        'applications': None if apps is None else [{'doc_id': a['bill_id'], 'amount_cents': a['amount_cents']}
                                                   for a in apps]})


# ---------------------------------------------------------------- routes

def _register(side, doc_path, pay_path, number_route):
    @route('GET', doc_path)
    def list_documents(ctx):
        return docs.list_docs(ctx.conn, side, ctx.q)

    if number_route:
        @route('GET', doc_path + '/next_number')
        def next_number(ctx):
            return {'number': ledger.next_ref(ctx.conn, side.doc_type, start=side.start)}

    @route('GET', doc_path + '/<id>')
    def read_document(ctx):
        return docs.get_doc(ctx.conn, side, ctx.p['id'])

    @route('POST', doc_path)
    def create_document(ctx):
        return docs.get_doc(ctx.conn, side, docs.save_doc(ctx.conn, side, ctx.body))

    @route('PUT', doc_path + '/<id>')
    def update_document(ctx):
        return docs.get_doc(ctx.conn, side, docs.save_doc(ctx.conn, side, ctx.body, ctx.p['id']))

    @route('DELETE', doc_path + '/<id>')
    def delete_document(ctx):
        d = docs.get_doc(ctx.conn, side, ctx.p['id'], detail=False)
        ledger.delete_txn(ctx.conn, d['txn_id'])
        return {'deleted': True}

    @route('GET', pay_path)
    def list_payments(ctx):
        return docs.list_pays(ctx.conn, side, ctx.q)

    @route('GET', pay_path + '/<id>')
    def read_payment(ctx):
        return docs.get_pay(ctx.conn, side, ctx.p['id'])

    @route('POST', pay_path)
    def create_payment(ctx):
        return docs.get_pay(ctx.conn, side, docs.save_pay(ctx.conn, side, docs.pay_data(side, ctx.body)))

    @route('PUT', pay_path + '/<id>')
    def update_payment(ctx):
        return docs.get_pay(ctx.conn, side, docs.save_pay(ctx.conn, side, docs.pay_data(side, ctx.body), ctx.p['id']))

    @route('DELETE', pay_path + '/<id>')
    def delete_payment(ctx):
        p = docs.get_pay(ctx.conn, side, ctx.p['id'])
        ledger.delete_txn(ctx.conn, p['txn_id'])
        return {'deleted': True}


_register(SALES, '/api/invoices', '/api/payments', True)
_register(PURCHASE, '/api/bills', '/api/bill_payments', False)
