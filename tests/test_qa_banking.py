"""QA tests: statement import (CSV / OFX / PDF text), bank-feed categorization, rules, matching, undo.

Feed sign convention: + is money into the account (bank deposit; card payment/refund),
- is money out (bank withdrawal; card charge).

Tests whose names start with test_BUG_ document defects that were reproduced; they are expected to fail
until the defect is fixed. Everything else is expected to pass.
"""
import base64
import unittest

from helpers import ApiCase

from sqb import parsers

OS = 'Office Supplies and Software'


class FeedCase(ApiCase):
    def imp(self, account, content, filename='stmt.csv', expect=200, **kw):
        body = {'account_id': self.acct(account), 'filename': filename, 'content': content}
        body.update(kw)
        return self.post('/api/feed/import', body, expect)

    def pv(self, account, content, filename='stmt.csv', expect=200, **kw):
        body = {'account_id': self.acct(account), 'filename': filename, 'content': content}
        body.update(kw)
        return self.post('/api/feed/preview', body, expect)

    def triples(self, pv):
        return [(r['date'], r['description'], r['amount']) for r in pv['rows']]

    def feed(self, account, status='review'):
        return self.get('/api/feed', account_id=self.acct(account), status=status)

    def rows(self, account, status='review'):
        return {r['description']: r for r in self.feed(account, status)['rows']}

    def cat(self, row, category=None, expect=200, **kw):
        body = dict(kw)
        if category:
            body['account_id'] = self.acct(category)
        return self.post('/api/feed/%d/categorize' % row['id'], body, expect)

    def sides(self, txn):
        """{account: signed amount} where debit is + and credit is -."""
        out = {}
        for l in txn['lines']:
            out[l['account']] = round(out.get(l['account'], 0) + l['debit'] - l['credit'], 2)
        return out

    def pl(self, start='2026-01-01', end='2026-12-31'):
        return self.get('/api/reports/profit_loss', start=start, end=end)

    def bs(self, end='2026-12-31'):
        return self.get('/api/reports/balance_sheet', end=end)

    def bsval(self, label, end='2026-12-31'):
        try:
            return self.val(self.bs(end), label)
        except KeyError:
            return 0.0

    def plval(self, label):
        try:
            return self.val(self.pl(), label)
        except KeyError:
            return 0.0

    def assertBalanced(self):
        bs = self.bs()
        self.assertEqual(self.val(bs, 'TOTAL ASSETS'), self.val(bs, 'TOTAL LIABILITIES AND EQUITY'))


# =====================================================================================================
# CSV layouts
# =====================================================================================================

class CsvLayouts(FeedCase):
    def test_chase_credit_card(self):
        csv = ('Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n'
               '01/03/2026,01/04/2026,DELTA AIR LINES,Travel,Sale,-420.00,\n'
               '01/08/2026,01/09/2026,AMAZON MKTPL*RT4GH2,Shopping,Sale,-80.15,\n'
               '01/12/2026,01/13/2026,STAPLES 00123,Shopping,Return,25.00,\n'
               '01/15/2026,01/15/2026,Payment Thank You-Mobile,,Payment,300.00,\n')
        pv = self.pv('Credit Card', csv)
        self.assertEqual(self.triples(pv), [
            ('2026-01-04', 'DELTA AIR LINES', -420.0), ('2026-01-09', 'AMAZON MKTPL*RT4GH2', -80.15),
            ('2026-01-13', 'STAPLES 00123', 25.0), ('2026-01-15', 'Payment Thank You-Mobile', 300.0)])
        self.assertEqual((pv['money_in'], pv['money_out']), (325.0, 500.15))

    def test_chase_checking(self):
        csv = ('Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n'
               'DEBIT,01/05/2026,"OFFICE DEPOT #123 SAN JOSE CA",-150.25,DEBIT_CARD,849.75,,\n'
               'CREDIT,01/06/2026,"ORIG CO NAME:STRIPE TRANSFER",2000.00,ACH_CREDIT,2849.75,,\n'
               'CHECK,01/07/2026,"CHECK 1041",-500.00,CHECK_PAID,2349.75,1041,\n'
               'DSLIP,01/08/2026,"DEPOSIT ID NUMBER 12345",750.00,DEPOSIT,3099.75,,\n')
        pv = self.pv('Checking', csv)
        self.assertEqual([r['amount'] for r in pv['rows']], [-150.25, 2000.0, -500.0, 750.0])
        self.assertEqual(pv['rows'][0]['description'], 'OFFICE DEPOT #123 SAN JOSE CA')
        self.assertEqual(pv['rows'][0]['date'], '2026-01-05')

    def test_bank_of_america_checking_with_summary_preamble(self):
        csv = ('Description,,Summary Amt.\n'
               'Beginning balance as of 01/01/2026,,"1,000.00"\n'
               'Total credits,,"2,000.00"\n'
               'Total debits,,"-1,650.25"\n'
               'Ending balance as of 01/31/2026,,"1,349.75"\n'
               '\n'
               'Date,Description,Amount,Running Bal.\n'
               '01/01/2026,Beginning balance as of 01/01/2026,,"1,000.00"\n'
               '01/05/2026,"OFFICE DEPOT 01/04 PURCHASE SAN JOSE, CA","-150.25","849.75"\n'
               '01/06/2026,"Counter Credit","2,000.00","2,849.75"\n'
               '01/10/2026,"RENT DES:ACH ID:123 INDN:ACME CO","-1,500.00","1,349.75"\n')
        pv = self.pv('Checking', csv)
        self.assertEqual(self.triples(pv), [
            ('2026-01-05', 'OFFICE DEPOT 01/04 PURCHASE SAN JOSE, CA', -150.25),
            ('2026-01-06', 'Counter Credit', 2000.0),
            ('2026-01-10', 'RENT DES:ACH ID:123 INDN:ACME CO', -1500.0)])

    def test_bank_of_america_credit_card(self):
        csv = ('Posted Date,Reference Number,Payee,Address,Amount\n'
               '01/04/2026,24492156004000012345678,"DELTA AIR LINES","ATLANTA GA",-420.00\n'
               '01/15/2026,74492156015000087654321,"PAYMENT - THANK YOU","",300.00\n')
        pv = self.pv('Credit Card', csv)
        self.assertEqual(self.triples(pv), [('2026-01-04', 'DELTA AIR LINES', -420.0),
                                            ('2026-01-15', 'PAYMENT - THANK YOU', 300.0)])

    def test_wells_fargo_no_header(self):
        csv = ('"01/05/2026","-150.25","*","","PURCHASE AUTHORIZED ON 01/04 OFFICE DEPOT SAN JOSE CA S123 CARD 9999"\n'
               '"01/06/2026","2000.00","*","","STRIPE TRANSFER ST-ABC ACME CO"\n'
               '"01/07/2026","-500.00","*","1041","CHECK # 1041"\n')
        pv = self.pv('Checking', csv)
        self.assertEqual([(r['date'], r['amount']) for r in pv['rows']],
                         [('2026-01-05', -150.25), ('2026-01-06', 2000.0), ('2026-01-07', -500.0)])
        self.assertTrue(pv['rows'][0]['description'].startswith('PURCHASE AUTHORIZED ON 01/04 OFFICE DEPOT'))
        self.assertEqual(pv['rows'][2]['description'], 'CHECK # 1041')

    def test_amex_charges_positive_need_flip(self):
        csv = ('Date,Description,Card Member,Account #,Amount\n'
               '01/03/2026,DELTA AIR LINES ATLANTA,JANE DOE,-51006,420.00\n'
               '01/15/2026,ONLINE PAYMENT - THANK YOU,JANE DOE,-51006,-300.00\n')
        pv = self.pv('Credit Card', csv, flip_sign=True)
        self.assertEqual(self.triples(pv), [('2026-01-03', 'DELTA AIR LINES ATLANTA', -420.0),
                                            ('2026-01-15', 'ONLINE PAYMENT - THANK YOU', 300.0)])

    def test_amex_extended_layout(self):
        csv = ('Date,Description,Amount,Extended Details,Appears On Your Statement As,Address,City/State,'
               'Zip Code,Country,Reference,Category\n'
               '01/03/2026,DELTA AIR LINES,420.00,"TKT 0061234567\nDELTA AIR LINES",DELTA AIR LINES,'
               '"1030 DELTA BLVD","ATLANTA\nGA",30354,UNITED STATES,\'320260030123456789\',Travel-Airline\n'
               '01/15/2026,ONLINE PAYMENT - THANK YOU,-300.00,,ONLINE PAYMENT - THANK YOU,,,,,'
               '\'320260150987654321\',\n')
        pv = self.pv('Credit Card', csv, flip_sign=True)
        self.assertEqual(self.triples(pv), [('2026-01-03', 'DELTA AIR LINES', -420.0),
                                            ('2026-01-15', 'ONLINE PAYMENT - THANK YOU', 300.0)])

    def test_capital_one_card_debit_credit_columns(self):
        csv = ('Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit\n'
               '2026-01-03,2026-01-04,1234,DELTA AIR LINES,Airfare,420.00,\n'
               '2026-01-15,2026-01-15,1234,CAPITAL ONE AUTOPAY PYMT,Payment/Credit,,300.00\n'
               '2026-01-20,2026-01-21,1234,STAPLES REFUND,Merchandise,,25.00\n')
        pv = self.pv('Credit Card', csv)
        self.assertEqual(self.triples(pv), [('2026-01-04', 'DELTA AIR LINES', -420.0),
                                            ('2026-01-15', 'CAPITAL ONE AUTOPAY PYMT', 300.0),
                                            ('2026-01-21', 'STAPLES REFUND', 25.0)])

    def test_capital_one_360_unsigned_amount_with_type_column(self):
        csv = ('Account Number,Transaction Description,Transaction Date,Transaction Type,Transaction Amount,Balance\n'
               '1234,Debit Card Purchase - OFFICE DEPOT,01/05/26,Debit,150.25,849.75\n'
               '1234,Deposit from STRIPE,01/06/26,Credit,2000.00,2849.75\n')
        pv = self.pv('Checking', csv)
        self.assertEqual(self.triples(pv), [('2026-01-05', 'Debit Card Purchase - OFFICE DEPOT', -150.25),
                                            ('2026-01-06', 'Deposit from STRIPE', 2000.0)])

    def test_citi_card_debit_positive_credit_negative(self):
        csv = ('Status,Date,Description,Debit,Credit,Member Name\n'
               'Cleared,01/03/2026,"DELTA AIR LINES ATLANTA GA",420.00,,JANE DOE\n'
               'Cleared,01/15/2026,"ONLINE PAYMENT, THANK YOU",,-300.00,JANE DOE\n')
        pv = self.pv('Credit Card', csv)
        self.assertEqual(self.triples(pv), [('2026-01-03', 'DELTA AIR LINES ATLANTA GA', -420.0),
                                            ('2026-01-15', 'ONLINE PAYMENT, THANK YOU', 300.0)])

    def test_discover_layout(self):
        csv = ('Trans. Date,Post Date,Description,Amount,Category\n'
               '01/03/2026,01/04/2026,"DELTA AIR LINES",420.00,"Travel/ Entertainment"\n'
               '01/15/2026,01/15/2026,"INTERNET PAYMENT - THANK YOU",-300.00,"Payments and Credits"\n')
        pv = self.pv('Credit Card', csv, flip_sign=True)
        self.assertEqual(self.triples(pv), [('2026-01-04', 'DELTA AIR LINES', -420.0),
                                            ('2026-01-15', 'INTERNET PAYMENT - THANK YOU', 300.0)])

    def test_withdrawals_deposits_columns_and_trailing_total(self):
        csv = ('Account Activity for 01/01/2026 - 01/31/2026\n'
               'Account: Business Checking ...1234\n'
               '\n'
               'Date,Check Number,Description,Withdrawals,Deposits,Balance\n'
               '01/05/2026,,OFFICE DEPOT,"$1,150.25",,"$8,849.75"\n'
               '01/06/2026,,CUSTOMER DEPOSIT,,"$2,000.00","$10,849.75"\n'
               '01/07/2026,1041,CHECK,$500.00,,"$10,349.75"\n'
               'Total,,,"$1,650.25","$2,000.00",\n')
        pv = self.pv('Checking', csv)
        self.assertEqual(self.triples(pv), [('2026-01-05', 'OFFICE DEPOT', -1150.25),
                                            ('2026-01-06', 'CUSTOMER DEPOSIT', 2000.0),
                                            ('2026-01-07', 'CHECK', -500.0)])
        self.assertEqual((pv['money_in'], pv['money_out']), (2000.0, 1650.25))

    def test_amount_formats(self):
        csv = ('Date,Description,Amount\n'
               '01/05/2026,PARENS,"(1,234.56)"\n'
               '01/06/2026,DOLLAR,"$2,000.00"\n'
               '01/07/2026,NEG DOLLAR,-$45.10\n'
               '01/08/2026,DOLLAR NEG,$-45.20\n'
               '01/09/2026,TRAILING MINUS,45.30-\n'
               '01/10/2026,PLUS,+12.00\n'
               '01/11/2026,WHOLE,-7\n'
               '01/12/2026,ONE DECIMAL,-7.5\n'
               '01/13/2026,ZERO,0.00\n'
               'Total,,"1,000.00"\n')
        pv = self.pv('Checking', csv)
        self.assertEqual([r['amount'] for r in pv['rows']],
                         [-1234.56, 2000.0, -45.1, -45.2, -45.3, 12.0, -7.0, -7.5])

    def test_date_formats(self):
        cases = [('2026-01-05', '2026-01-05'), ('01/05/2026', '2026-01-05'), ('1/5/2026', '2026-01-05'),
                 ('01/05/26', '2026-01-05'), ('1/5/26', '2026-01-05'), ('01-05-2026', '2026-01-05'),
                 ('2026/01/05', '2026-01-05'), ('05-Jan-2026', '2026-01-05'), ('05-JAN-26', '2026-01-05'),
                 ('"Jan 5, 2026"', '2026-01-05'), ('"January 5, 2026"', '2026-01-05'),
                 ('5 Jan 2026', '2026-01-05'), ('20260105', '2026-01-05'), ('01.05.2026', '2026-01-05'),
                 ('2026-01-05T08:30:00', '2026-01-05'), ('2026-01-05 08:30:00', '2026-01-05'),
                 ('01/05/2026 12:00:00 AM', '2026-01-05'), ('12/31/2025', '2025-12-31')]
        for raw, want in cases:
            with self.subTest(raw=raw):
                pv = self.pv('Checking', 'Date,Description,Amount\n%s,X,-1.00\n' % raw)
                self.assertEqual(pv['rows'][0]['date'], want)

    def test_other_delimiters_and_bom(self):
        for d in ('\t', ';', '|'):
            with self.subTest(delim=d):
                csv = '﻿' + d.join(['Date', 'Description', 'Amount']) + '\n' + \
                      d.join(['01/05/2026', 'OFFICE DEPOT', '-150.25']) + '\n'
                self.assertEqual(self.triples(self.pv('Checking', csv)), [('2026-01-05', 'OFFICE DEPOT', -150.25)])

    def test_content_b64_and_latin1(self):
        raw = 'Date,Description,Amount\r\n01/05/2026,CAF\xc9 ROUGE,-12.00\r\n'.encode('latin-1')
        pv = self.post('/api/feed/preview', {'account_id': self.acct('Checking'), 'filename': 'a.csv',
                                             'content_b64': base64.b64encode(raw).decode()})
        self.assertEqual(self.triples(pv), [('2026-01-05', 'CAF\xc9 ROUGE', -12.0)])

    def test_unparseable_files_are_400(self):
        self.pv('Checking', '', expect=400)
        self.pv('Checking', 'hello world\nnothing here\n', expect=400)
        self.pv('Checking', 'Date,Description,Amount\n', expect=400)
        self.pv('Checking', 'Date,Description\n01/05/2026,X\n', expect=400)
        self.pv('Sales', 'Date,Description,Amount\n01/05/2026,X,-1\n', expect=400)   # not a bank/card account

    # ---- defects ------------------------------------------------------------------------------------

    def test_BUG_chase_card_file_with_only_payments_and_returns_flips_payment_sign(self):
        """A signed-amount export whose rows all happen to be positive (a card statement with only a
        payment and a refund) is mistaken for 'unsigned amounts + type column' and the payment is negated."""
        csv = ('Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n'
               '01/13/2026,01/13/2026,STAPLES 00123,Shopping,Return,25.00,\n'
               '01/15/2026,01/15/2026,Payment Thank You-Mobile,,Payment,300.00,\n')
        pv = self.pv('Credit Card', csv)
        self.assertEqual([r['amount'] for r in pv['rows']], [25.0, 300.0])

    def test_BUG_debit_credit_indicator_column_ignored(self):
        """Unsigned Amount + a 'Debit/Credit' indicator column: the indicator is swallowed as the
        'debit' amount column, so every withdrawal is imported as a deposit."""
        csv = ('Date,Description,Amount,Debit/Credit\n'
               '01/05/2026,OFFICE DEPOT,150.25,Debit\n'
               '01/06/2026,CUSTOMER DEPOSIT,2000.00,Credit\n')
        pv = self.pv('Checking', csv)
        self.assertEqual([r['amount'] for r in pv['rows']], [-150.25, 2000.0])

    def test_BUG_utf16_or_nul_bytes_crash_with_500(self):
        """Excel 'Unicode Text' exports are UTF-16; NUL bytes reach csv.reader and raise -> HTTP 500."""
        raw = 'Date,Description,Amount\r\n01/05/2026,OFFICE DEPOT,-150.25\r\n'.encode('utf-16')
        status, data = self.app.handle('POST', '/api/feed/preview', {}, {
            'account_id': self.acct('Checking'), 'filename': 'a.csv',
            'content_b64': base64.b64encode(raw).decode()})
        self.assertNotEqual(status, 500, data)


# =====================================================================================================
# OFX / QFX
# =====================================================================================================

OFX_SGML = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20260131120000[0:GMT]<LANGUAGE>ENG
<FI><ORG>B1<FID>10898</FI><INTU.BID>10898</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>021000021<ACCTID>123456789<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260101120000[0:GMT]<DTEND>20260131120000[0:GMT]
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260105120000[0:GMT]<TRNAMT>-150.25<FITID>202601050001<NAME>OFFICE DEPOT #123<MEMO>SAN JOSE CA
</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260106120000[0:GMT]<TRNAMT>2000.00<FITID>202601060001<NAME>AT&amp;T REFUND
</STMTTRN>
<STMTTRN><TRNTYPE>CHECK<DTPOSTED>20260107<TRNAMT>-500.00<FITID>202601070001<CHECKNUM>1041<NAME>CHECK 1041
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>2349.75<DTASOF>20260131120000[0:GMT]</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

OFX_XML_CC = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<?OFX OFXHEADER="200" VERSION="211" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>
<OFX>
  <CREDITCARDMSGSRSV1>
    <CCSTMTTRNRS>
      <TRNUID>0</TRNUID>
      <CCSTMTRS>
        <CURDEF>USD</CURDEF>
        <CCACCTFROM><ACCTID>XXXXXXXXXXXX1234</ACCTID></CCACCTFROM>
        <BANKTRANLIST>
          <DTSTART>20260101</DTSTART>
          <DTEND>20260131</DTEND>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260104000000.000[-5:EST]</DTPOSTED>
            <TRNAMT>-420.00</TRNAMT>
            <FITID>320260040111</FITID>
            <NAME>DELTA AIR LINES</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>CREDIT</TRNTYPE>
            <DTPOSTED>20260115000000.000[-5:EST]</DTPOSTED>
            <TRNAMT>300.00</TRNAMT>
            <FITID>320260150222</FITID>
            <MEMO>PAYMENT THANK YOU</MEMO>
          </STMTTRN>
        </BANKTRANLIST>
        <LEDGERBAL><BALAMT>-120.00</BALAMT><DTASOF>20260131</DTASOF></LEDGERBAL>
      </CCSTMTRS>
    </CCSTMTTRNRS>
  </CREDITCARDMSGSRSV1>
</OFX>
"""


class Ofx(FeedCase):
    def test_sgml_bank(self):
        pv = self.pv('Checking', OFX_SGML, filename='chk.qfx')
        self.assertEqual(pv['format'], 'ofx')
        self.assertEqual(self.triples(pv), [('2026-01-05', 'OFFICE DEPOT #123 SAN JOSE CA', -150.25),
                                            ('2026-01-06', 'AT&T REFUND', 2000.0),
                                            ('2026-01-07', 'CHECK 1041', -500.0)])

    def test_sgml_detected_without_extension(self):
        self.assertEqual(self.pv('Checking', OFX_SGML, filename='download')['format'], 'ofx')

    def test_sgml_single_line(self):
        one = OFX_SGML.replace('\n', '')
        self.assertEqual(len(self.pv('Checking', one, filename='chk.ofx')['rows']), 3)

    def test_xml_credit_card(self):
        pv = self.pv('Credit Card', OFX_XML_CC, filename='cc.qfx')
        self.assertEqual(self.triples(pv), [('2026-01-04', 'DELTA AIR LINES', -420.0),
                                            ('2026-01-15', 'PAYMENT THANK YOU', 300.0)])

    def test_reimport_uses_fitid(self):
        self.assertEqual(self.imp('Checking', OFX_SGML, filename='a.qfx')['imported'], 3)
        again = self.imp('Checking', OFX_SGML, filename='a.qfx')
        self.assertEqual((again['imported'], again['duplicates']), (0, 3))
        # the bank re-words the description in a later download: still the same FITID, so still a duplicate
        again = self.imp('Checking', OFX_SGML.replace('OFFICE DEPOT #123', 'OFFICE DEPOT 0123'), filename='b.qfx')
        self.assertEqual((again['imported'], again['duplicates']), (0, 3))

    def test_empty_ofx_is_400(self):
        self.pv('Checking', 'OFXHEADER:100\n<OFX><BANKMSGSRSV1></BANKMSGSRSV1></OFX>', filename='a.ofx', expect=400)

    def test_BUG_same_statement_as_csv_then_ofx_is_not_detected_as_duplicate(self):
        """Downloading the same period once as CSV and once as QFX double-loads the feed (QuickBooks
        compares date + amount + description, not only the bank id)."""
        csv = ('Date,Description,Amount\n01/05/2026,OFFICE DEPOT #123 SAN JOSE CA,-150.25\n'
               '01/06/2026,AT&T REFUND,2000.00\n01/07/2026,CHECK 1041,-500.00\n')
        self.assertEqual(self.imp('Checking', csv)['imported'], 3)
        again = self.imp('Checking', OFX_SGML, filename='a.qfx')
        self.assertEqual((again['imported'], again['duplicates']), (0, 3))


# =====================================================================================================
# PDF text
# =====================================================================================================

CHASE_CHECKING_PDF = """JPMorgan Chase Bank, N.A.
January 01, 2026 through January 30, 2026
Account Number: 000000123456789
CHECKING SUMMARY
INSTANCES AMOUNT
Beginning Balance $1,000.00
Deposits and Additions 2 2,750.00
ATM & Debit Card Withdrawals 2 -195.25
Electronic Withdrawals 1 -300.00
Ending Balance 5 $3,254.75
DEPOSITS AND ADDITIONS
DATE DESCRIPTION AMOUNT
01/06 Orig CO Name:Stripe Orig ID:1800948598 Desc Date:260106 CO Entry $2,000.00
01/08 Deposit 1234567890 750.00
Total Deposits and Additions $2,750.00
ATM & DEBIT CARD WITHDRAWALS
DATE DESCRIPTION AMOUNT
01/05 Card Purchase 01/04 Office Depot #123 San Jose CA Card 9999 $150.25
01/10 Card Purchase 01/09 Shell Oil 5551 San Jose CA Card 9999 45.00
Total ATM & Debit Card Withdrawals $195.25
ELECTRONIC WITHDRAWALS
DATE DESCRIPTION AMOUNT
01/15 Chase Card Ending IN 1234 Autopay 300.00
Total Electronic Withdrawals $300.00
"""


class PdfText(unittest.TestCase):
    def rows(self, text, **kw):
        return [(r['date'], r['description'], r['amount']) for r in parsers.parse_pdf_text(text, **kw)]

    def test_chase_checking_sections(self):
        rows = self.rows(CHASE_CHECKING_PDF)
        self.assertEqual([(d, a) for d, _, a in rows], [
            ('2026-01-06', 200000), ('2026-01-08', 75000), ('2026-01-05', -15025), ('2026-01-10', -4500),
            ('2026-01-15', -30000)])
        self.assertEqual(rows[2][1], 'Card Purchase 01/04 Office Depot #123 San Jose CA Card 9999')

    def test_bofa_checking_signed_amounts_with_full_dates(self):
        text = ('Your Business Fundamentals Checking\nfor January 1, 2026 to January 31, 2026\n'
                'Deposits and other credits\nDate Description Amount\n'
                '01/06/26 STRIPE DES:TRANSFER ID:ST-ABC INDN:ACME CO 2,000.00\n'
                'Total deposits and other credits $2,000.00\n'
                'Withdrawals and other debits\nDate Description Amount\n'
                '01/05/26 CHECKCARD 0104 OFFICE DEPOT SAN JOSE CA -150.25\n'
                '01/10/26 RENT DES:ACH ID:123 INDN:ACME CO -1,500.00\n'
                'Total withdrawals and other debits -$1,650.25\n'
                'Service fees\nDate Transaction description Amount\n'
                '01/31/26 Monthly Fee Business Fundamentals -16.00\n'
                'Total service fees -$16.00\n')
        self.assertEqual([(d, a) for d, _, a in self.rows(text)], [
            ('2026-01-06', 200000), ('2026-01-05', -15025), ('2026-01-10', -150000), ('2026-01-31', -1600)])

    def test_card_statement_with_sections(self):
        text = ('ACCOUNT ACTIVITY\nOpening/Closing Date 01/01/26 - 01/31/26\n'
                'PAYMENTS AND OTHER CREDITS\n'
                '01/15 Payment Thank You-Mobile -300.00\n'
                '01/13 STAPLES 00123 SAN JOSE CA -25.00\n'
                'PURCHASE\n'
                '01/03 DELTA AIR LINES ATLANTA GA 420.00\n'
                '01/08 AMAZON MKTPL*RT4GH2 Amzn.com/bill WA 80.15\n')
        self.assertEqual([(d, a) for d, _, a in self.rows(text, is_cc=True)], [
            ('2026-01-15', 30000), ('2026-01-13', 2500), ('2026-01-03', -42000), ('2026-01-08', -8015)])

    def test_card_statement_without_sections_charges_positive(self):
        text = ('Statement closing date 01/31/2026\n'
                'Trans Date Post Date Description Amount\n'
                '01/03 01/04 DELTA AIR LINES ATLANTA GA $420.00\n'
                '01/15 01/15 ONLINE PAYMENT THANK YOU -$300.00\n'
                '01/20 01/21 STAPLES REFUND (25.00)\n')
        self.assertEqual([(d, a) for d, _, a in self.rows(text, is_cc=True)], [
            ('2026-01-03', -42000), ('2026-01-15', 30000), ('2026-01-20', 2500)])

    def test_explicit_year(self):
        self.assertEqual(self.rows('Withdrawals\n03/02 RENT 100.00\n', default_year=2024),
                         [('2024-03-02', 'RENT', -10000)])

    # ---- defects ------------------------------------------------------------------------------------

    def test_BUG_card_pdf_amount_with_CR_suffix_becomes_a_charge(self):
        """'300.00 CR' on a card statement is a payment/credit (+), but the is_cc flip turns it into -300."""
        text = ('Statement closing date 01/31/2026\n'
                '01/03 DELTA AIR LINES ATLANTA GA 420.00\n'
                '01/15 PAYMENT - THANK YOU 300.00 CR\n')
        self.assertEqual([a for _, _, a in self.rows(text, is_cc=True)], [-42000, 30000])

    def test_BUG_statement_spanning_new_year_puts_december_in_wrong_year(self):
        """A statement for Dec 13 2025 - Jan 14 2026 lists MM/DD only; every row gets the same year."""
        text = ('December 13, 2025 through January 14, 2026\n'
                'Statement Date 01/14/2026\n'
                'ELECTRONIC WITHDRAWALS\n'
                '12/20 Rent Payment 1,500.00\n'
                '01/05 Office Depot 150.25\n')
        self.assertEqual([d for d, _, _ in self.rows(text)], ['2025-12-20', '2026-01-05'])

    def test_BUG_daily_ending_balance_table_imported_as_transactions(self):
        """Chase statements end with a multi-column 'DAILY ENDING BALANCE' table; its rows are read
        as extra withdrawals."""
        text = CHASE_CHECKING_PDF + ('DAILY ENDING BALANCE\nDATE AMOUNT DATE AMOUNT\n'
                                     '01/05 $849.75 01/10 3,554.75\n'
                                     '01/06 2,849.75 01/15 3,254.75\n'
                                     '01/08 3,599.75\n')
        self.assertEqual(len(self.rows(text)), 5)

    def test_BUG_wrapped_description_line_flips_section_sign(self):
        """A wrapped second line of a deposit description that contains a word such as 'Payment'
        is taken for a section heading; later deposits in the section become withdrawals."""
        text = ('January 01, 2026 through January 30, 2026\n'
                'DEPOSITS AND ADDITIONS\n'
                'DATE DESCRIPTION AMOUNT\n'
                '01/06 Orig CO Name:Stripe Orig ID:1800948598 Desc Date:260106 CO Entry $2,000.00\n'
                'Descr:Payment Sec:CCD Trace#:091000012345678 Eed:260106\n'
                '01/08 Deposit 1234567890 750.00\n'
                'Total Deposits and Additions $2,750.00\n')
        self.assertEqual([a for _, _, a in self.rows(text)], [200000, 75000])

    def test_BUG_single_table_bank_statement_all_rows_become_deposits(self):
        """Wells Fargo style: one chronological table with Deposits/Withdrawals columns and a running
        balance. The column header is taken for a 'deposits' section, so withdrawals import as deposits."""
        text = ('Statement period activity summary 01/01/2026 - 01/31/2026\n'
                'Transaction history\n'
                'Date Number Description Deposits/Additions Withdrawals/Subtractions Ending daily balance\n'
                '1/5 Purchase authorized on 01/04 Office Depot San Jose CA 150.25 849.75\n'
                '1/6 Stripe Transfer St-Abc Acme Co 2,000.00 2,849.75\n'
                '1/7 1041 Check 500.00 2,349.75\n')
        self.assertEqual([a for _, _, a in self.rows(text)], [-15025, 200000, -50000])


# =====================================================================================================
# Duplicate detection
# =====================================================================================================

JAN = ('Date,Description,Amount\n'
       '01/05/2026,OFFICE DEPOT #123,-150.25\n'
       '01/06/2026,CUSTOMER DEPOSIT,2000.00\n'
       '01/10/2026,SHELL OIL 5551,-45.00\n'
       '01/15/2026,CC PAYMENT THANK YOU,-300.00\n')


class Duplicates(FeedCase):
    def test_reimport_and_overlap(self):
        self.assertEqual(self.imp('Checking', JAN)['imported'], 4)
        overlap = ('Date,Description,Amount\n'
                   '01/10/2026,SHELL OIL 5551,-45.00\n'
                   '01/15/2026,CC PAYMENT THANK YOU,-300.00\n'
                   '01/20/2026,OFFICE DEPOT #987,-20.00\n')
        pv = self.pv('Checking', overlap)
        self.assertEqual((pv['count'], pv['new_count'], pv['duplicate_count'], pv['money_out']), (3, 1, 2, 20.0))
        self.assertEqual([r['duplicate'] for r in pv['rows']], [True, True, False])
        r = self.imp('Checking', overlap)
        self.assertEqual((r['imported'], r['duplicates']), (1, 2))
        self.assertEqual(self.feed('Checking')['counts']['review'], 5)

    def test_whitespace_and_case_differences_are_still_duplicates(self):
        self.imp('Checking', JAN)
        r = self.imp('Checking', 'Date,Description,Amount\n2026-01-05,office   depot #123,"-150.25"\n')
        self.assertEqual((r['imported'], r['duplicates']), (0, 1))

    def test_identical_same_day_transactions_are_both_kept(self):
        csv = ('Date,Description,Amount\n'
               '01/05/2026,STARBUCKS #1234,-4.75\n'
               '01/05/2026,STARBUCKS #1234,-4.75\n'
               '01/05/2026,STARBUCKS #1234,-4.75\n')
        pv = self.pv('Checking', csv)
        self.assertEqual((pv['new_count'], pv['duplicate_count']), (3, 0))
        self.assertEqual(self.imp('Checking', csv)['imported'], 3)
        again = self.imp('Checking', csv)
        self.assertEqual((again['imported'], again['duplicates']), (0, 3))
        # an overlapping file that shows only one of them adds nothing
        one = 'Date,Description,Amount\n01/05/2026,STARBUCKS #1234,-4.75\n'
        self.assertEqual(self.imp('Checking', one)['imported'], 0)
        # a later file that shows four of them adds exactly one
        self.assertEqual(self.imp('Checking', csv + '01/05/2026,STARBUCKS #1234,-4.75\n')['imported'], 1)
        self.assertEqual(self.feed('Checking')['counts']['review'], 4)

    def test_handled_rows_still_count_as_duplicates(self):
        self.imp('Checking', JAN)
        rows = self.rows('Checking')
        self.cat(rows['OFFICE DEPOT #123'], OS)
        self.post('/api/feed/%d/exclude' % rows['SHELL OIL 5551']['id'])
        again = self.imp('Checking', JAN)
        self.assertEqual((again['imported'], again['duplicates']), (0, 4))
        self.assertEqual(self.plval(OS), 150.25)
        # ...and still after an undo
        self.post('/api/feed/%d/undo' % rows['OFFICE DEPOT #123']['id'])
        self.assertEqual(self.imp('Checking', JAN)['imported'], 0)
        self.assertEqual(self.feed('Checking')['counts'], {'review': 3, 'posted': 0, 'excluded': 1})

    def test_duplicates_are_per_account(self):
        self.imp('Checking', JAN)
        self.assertEqual(self.imp('Savings', JAN)['imported'], 4)

    def test_flip_sign_reimport_is_duplicate(self):
        csv = 'Date,Description,Amount\n01/03/2026,AIRLINE,420.00\n'
        self.assertEqual(self.imp('Credit Card', csv, flip_sign=True)['imported'], 1)
        self.assertEqual(self.imp('Credit Card', csv, flip_sign=True)['imported'], 0)
        self.assertEqual(self.feed('Credit Card')['rows'][0]['amount'], -420.0)


# =====================================================================================================
# Ledger sides and reports
# =====================================================================================================

CC = ('Date,Description,Amount\n'
      '01/03/2026,DELTA AIR LINES,-420.00\n'
      '01/12/2026,STAPLES REFUND,25.00\n'
      '01/15/2026,PAYMENT THANK YOU,300.00\n')


class LedgerSides(FeedCase):
    def test_bank_withdrawal_is_expense(self):
        self.imp('Checking', JAN)
        t = self.cat(self.rows('Checking')['OFFICE DEPOT #123'], OS)
        self.assertEqual(t['type'], 'Expense')
        self.assertEqual(t['date'], '2026-01-05')
        self.assertEqual(self.sides(t), {'Checking': -150.25, OS: 150.25})
        self.assertEqual(self.plval(OS), 150.25)
        self.assertEqual(self.plval('Net Income'), -150.25)
        self.assertEqual(self.bsval('Checking'), -150.25)
        self.assertBalanced()

    def test_bank_deposit_is_income(self):
        self.imp('Checking', JAN)
        t = self.cat(self.rows('Checking')['CUSTOMER DEPOSIT'], 'Sales')
        self.assertEqual(t['type'], 'Deposit')
        self.assertEqual(self.sides(t), {'Checking': 2000.0, 'Sales': -2000.0})
        self.assertEqual(self.plval('Sales'), 2000.0)
        self.assertEqual(self.bsval('Checking'), 2000.0)
        self.assertBalanced()

    def test_bank_refund_reduces_expense(self):
        self.imp('Checking', 'Date,Description,Amount\n01/05/2026,OFFICE DEPOT,-150.25\n01/09/2026,OFFICE DEPOT REFUND,50.25\n')
        rows = self.rows('Checking')
        self.cat(rows['OFFICE DEPOT'], OS)
        t = self.cat(rows['OFFICE DEPOT REFUND'], OS)
        self.assertEqual(self.sides(t), {'Checking': 50.25, OS: -50.25})
        self.assertEqual(self.plval(OS), 100.0)
        self.assertEqual(self.bsval('Checking'), -100.0)

    def test_owner_contribution_and_loan(self):
        self.imp('Checking', 'Date,Description,Amount\n01/02/2026,OWNER TRANSFER IN,5000.00\n01/03/2026,LOAN PMT,-400.00\n')
        rows = self.rows('Checking')
        self.cat(rows['OWNER TRANSFER IN'], "Owner's Investment")
        self.cat(rows['LOAN PMT'], 'Notes Payable')
        self.assertEqual(self.plval('Net Income'), 0.0)
        self.assertEqual(self.bsval("Owner's Investment"), 5000.0)
        self.assertEqual(self.bsval('Notes Payable'), -400.0)
        self.assertEqual(self.bsval('Checking'), 4600.0)
        self.assertBalanced()

    def test_card_charge_refund_and_reports(self):
        self.imp('Credit Card', CC)
        rows = self.rows('Credit Card')
        t = self.cat(rows['DELTA AIR LINES'], 'Travel')
        self.assertEqual(t['type'], 'Expense')
        self.assertEqual(self.sides(t), {'Credit Card': -420.0, 'Travel': 420.0})
        t = self.cat(rows['STAPLES REFUND'], OS)
        self.assertEqual(t['type'], 'Credit Card Credit')
        self.assertEqual(self.sides(t), {'Credit Card': 25.0, OS: -25.0})
        self.assertEqual(self.plval('Travel'), 420.0)
        self.assertEqual(self.plval(OS), -25.0)
        self.assertEqual(self.plval('Net Income'), -395.0)
        self.assertEqual(self.bsval('Credit Card'), 395.0)       # liability owed
        self.assertEqual(self.feed('Credit Card')['book_balance'], 395.0)
        self.assertBalanced()

    def test_card_payment_from_bank_side(self):
        self.imp('Checking', JAN)
        t = self.cat(self.rows('Checking')['CC PAYMENT THANK YOU'], 'Credit Card')
        self.assertEqual(t['type'], 'Credit Card Payment')
        self.assertEqual(self.sides(t), {'Checking': -300.0, 'Credit Card': 300.0})
        self.assertEqual(self.plval('Net Income'), 0.0)
        self.assertEqual(self.bsval('Credit Card'), -300.0)
        self.assertBalanced()

    def test_card_payment_from_card_side_then_match_on_bank(self):
        self.imp('Checking', JAN)
        self.imp('Credit Card', CC)
        t = self.cat(self.rows('Credit Card')['PAYMENT THANK YOU'], 'Checking')
        self.assertEqual(t['type'], 'Credit Card Payment')
        self.assertEqual(self.sides(t), {'Checking': -300.0, 'Credit Card': 300.0})
        brow = self.rows('Checking')['CC PAYMENT THANK YOU']
        self.assertEqual([m['txn_id'] for m in brow['matches']], [t['id']])
        m = self.post('/api/feed/%d/match' % brow['id'], {'line_id': brow['matches'][0]['line_id']})
        self.assertEqual(m['id'], t['id'])
        self.assertEqual(self.bsval('Checking'), -300.0)          # counted once
        posted = self.rows('Checking', 'posted')['CC PAYMENT THANK YOU']
        self.assertTrue(posted['matched'])
        self.assertEqual(posted['category'], 'Credit Card')
        # the matched line is no longer offered to any other statement row
        self.assertEqual(self.rows('Credit Card', 'posted')['PAYMENT THANK YOU']['matched'], False)

    def test_bank_to_bank_transfer_and_match(self):
        self.imp('Checking', 'Date,Description,Amount\n01/20/2026,ONLINE TRANSFER TO SAV 4321,-1000.00\n')
        self.imp('Savings', 'Date,Description,Amount\n01/20/2026,ONLINE TRANSFER FROM CHK 1234,1000.00\n')
        t = self.cat(self.feed('Checking')['rows'][0], 'Savings')
        self.assertEqual(t['type'], 'Transfer')
        self.assertEqual(self.sides(t), {'Checking': -1000.0, 'Savings': 1000.0})
        srow = self.feed('Savings')['rows'][0]
        self.assertEqual(len(srow['matches']), 1)
        self.post('/api/feed/%d/match' % srow['id'], {'line_id': srow['matches'][0]['line_id']})
        self.assertEqual((self.bsval('Checking'), self.bsval('Savings')), (-1000.0, 1000.0))
        self.assertEqual(self.plval('Net Income'), 0.0)
        # undoing the side that created the transfer removes it and frees the matched side too
        self.post('/api/feed/%d/undo' % self.feed('Checking', 'posted')['rows'][0]['id'])
        self.assertEqual((self.bsval('Checking'), self.bsval('Savings')), (0.0, 0.0))
        self.assertEqual(self.feed('Savings')['counts']['review'], 1)
        self.assertEqual(self.feed('Checking')['counts']['review'], 1)

    def test_category_restrictions(self):
        self.imp('Checking', JAN)
        row = self.rows('Checking')['CUSTOMER DEPOSIT']
        self.cat(row, 'Checking', expect=400)                 # same account
        self.cat(row, 'Accounts Receivable', expect=400)      # must use match / receive payment
        self.cat(row, expect=400)                             # nothing chosen
        self.post('/api/feed/%d/categorize' % row['id'], {'account_id': 999999}, expect=400)
        self.cat(row, 'Sales', name_id=424242, expect=400)    # unknown name
        self.assertEqual(self.feed('Checking')['counts']['review'], 4)
        self.assertEqual(self.get('/api/journals'), [])

    def test_name_and_memo_are_recorded(self):
        v = self.post('/api/vendors', {'name': 'Office Depot'})
        self.imp('Checking', JAN)
        t = self.cat(self.rows('Checking')['OFFICE DEPOT #123'], OS, name_id=v['id'], memo='printer paper')
        self.assertEqual((t['name'], t['memo']), ('Office Depot', 'printer paper'))
        posted = self.rows('Checking', 'posted')['OFFICE DEPOT #123']
        self.assertEqual((posted['name'], posted['category'], posted['matched']), ('Office Depot', OS, False))

    def test_reports_respect_dates(self):
        self.imp('Checking', 'Date,Description,Amount\n12/30/2025,RENT DEC,-1000.00\n01/30/2026,RENT JAN,-1100.00\n')
        for r in self.feed('Checking')['rows']:
            self.cat(r, 'Rent and Lease')
        self.assertEqual(self.val(self.pl('2026-01-01', '2026-01-31'), 'Rent and Lease'), 1100.0)
        self.assertEqual(self.val(self.pl('2025-12-01', '2025-12-31'), 'Rent and Lease'), 1000.0)
        self.assertEqual(self.bsval('Checking', '2025-12-31'), -1000.0)
        self.assertEqual(self.bsval('Checking', '2026-01-31'), -2100.0)


class Splits(FeedCase):
    def test_three_way_split_with_cents(self):
        self.imp('Checking', 'Date,Description,Amount\n01/05/2026,COSTCO WHSE #123,-100.00\n')
        row = self.feed('Checking')['rows'][0]
        t = self.cat(row, splits=[{'account_id': self.acct(OS), 'amount': '33.33', 'memo': 'paper'},
                                  {'account_id': self.acct('Meals and Entertainment'), 'amount': 33.33},
                                  {'account_id': self.acct('Repairs and Maintenance'), 'amount': 33.34}])
        self.assertEqual(t['type'], 'Expense')
        self.assertEqual(self.sides(t), {'Checking': -100.0, OS: 33.33, 'Meals and Entertainment': 33.33,
                                         'Repairs and Maintenance': 33.34})
        self.assertEqual(self.rows('Checking', 'posted')['COSTCO WHSE #123']['category'], '-Split-')
        self.assertEqual(self.plval('Net Income'), -100.0)
        self.assertBalanced()

    def test_split_must_add_up_exactly(self):
        self.imp('Checking', 'Date,Description,Amount\n01/05/2026,COSTCO,-100.00\n')
        row = self.feed('Checking')['rows'][0]
        a, b = self.acct(OS), self.acct('Travel')
        self.cat(row, splits=[{'account_id': a, 'amount': 60}, {'account_id': b, 'amount': 39.99}], expect=400)
        self.cat(row, splits=[{'account_id': a, 'amount': 60}, {'account_id': b, 'amount': 40.01}], expect=400)
        self.cat(row, splits=[{'account_id': a, 'amount': -100}], expect=400)
        self.cat(row, splits=[{'account_id': a, 'amount': 0}], expect=400)
        self.cat(row, splits=[{'account_id': a, 'amount': 'abc'}], expect=400)
        self.assertEqual(self.feed('Checking')['counts']['review'], 1)
        self.assertEqual(self.bsval('Checking'), 0.0)

    def test_deposit_split_income_less_fee(self):
        # QuickBooks style: gross sale 103.00 less a 3.00 processing fee = 100.00 deposited
        self.imp('Checking', 'Date,Description,Amount\n01/06/2026,STRIPE TRANSFER,100.00\n')
        row = self.feed('Checking')['rows'][0]
        t = self.cat(row, splits=[{'account_id': self.acct('Sales'), 'amount': 103},
                                  {'account_id': self.acct('Bank Charges and Fees'), 'amount': -3}])
        self.assertEqual(self.sides(t), {'Checking': 100.0, 'Sales': -103.0, 'Bank Charges and Fees': 3.0})
        self.assertEqual((self.plval('Sales'), self.plval('Bank Charges and Fees')), (103.0, 3.0))
        self.assertEqual(self.bsval('Checking'), 100.0)
        self.assertBalanced()

    def test_card_charge_split_and_undo(self):
        self.imp('Credit Card', CC)
        row = self.rows('Credit Card')['DELTA AIR LINES']
        t = self.cat(row, splits=[{'account_id': self.acct('Travel'), 'amount': 400},
                                  {'account_id': self.acct('Meals and Entertainment'), 'amount': 20}])
        self.assertEqual(self.sides(t), {'Credit Card': -420.0, 'Travel': 400.0, 'Meals and Entertainment': 20.0})
        self.post('/api/feed/%d/undo' % row['id'])
        self.assertEqual((self.plval('Travel'), self.bsval('Credit Card')), (0.0, 0.0))
        self.call('GET', '/api/transactions/%d' % t['id'], expect=404)


# =====================================================================================================
# Rules and history suggestions
# =====================================================================================================

class Rules(FeedCase):
    def rule(self, text, category, direction='any', **kw):
        body = {'match_text': text, 'direction': direction, 'account_id': self.acct(category)}
        body.update(kw)
        return self.post('/api/rules', body)

    def sug(self, account, desc):
        return self.rows(account)[desc]['suggestion']

    def test_rule_is_case_insensitive_substring(self):
        self.rule('oFFice dePOT', OS)
        self.imp('Checking', JAN)
        s = self.sug('Checking', 'OFFICE DEPOT #123')
        self.assertEqual((s['account_id'], s['source']), (self.acct(OS), 'rule'))
        self.assertIsNone(self.sug('Checking', 'SHELL OIL 5551'))

    def test_rule_direction(self):
        csv = 'Date,Description,Amount\n01/05/2026,AMAZON PURCHASE,-30.00\n01/09/2026,AMAZON REFUND,30.00\n'
        self.imp('Checking', csv)
        r_out = self.rule('amazon', OS, 'out')
        self.assertEqual(self.sug('Checking', 'AMAZON PURCHASE')['rule_id'], r_out['id'])
        self.assertIsNone(self.sug('Checking', 'AMAZON REFUND'))
        r_in = self.rule('amazon', 'Sales', 'in')
        self.assertEqual(self.sug('Checking', 'AMAZON REFUND')['rule_id'], r_in['id'])
        self.assertEqual(self.sug('Checking', 'AMAZON PURCHASE')['rule_id'], r_out['id'])
        self.delete('/api/rules/%d' % r_out['id'])
        self.delete('/api/rules/%d' % r_in['id'])
        r_any = self.rule('amazon', 'Travel')
        self.assertEqual(self.sug('Checking', 'AMAZON REFUND')['rule_id'], r_any['id'])
        self.assertEqual(self.sug('Checking', 'AMAZON PURCHASE')['rule_id'], r_any['id'])

    def test_rule_direction_on_card_follows_feed_sign(self):
        self.imp('Credit Card', CC)
        self.rule('delta', 'Travel', 'out')
        self.rule('payment', 'Checking', 'in')
        self.assertEqual(self.sug('Credit Card', 'DELTA AIR LINES')['account_id'], self.acct('Travel'))
        self.assertEqual(self.sug('Credit Card', 'PAYMENT THANK YOU')['account_id'], self.acct('Checking'))

    def test_first_rule_wins_and_rule_beats_history(self):
        csv = ('Date,Description,Amount\n01/05/2026,OFFICE DEPOT #1,-10.00\n01/06/2026,OFFICE DEPOT #2,-20.00\n')
        self.imp('Checking', csv)
        self.cat(self.rows('Checking')['OFFICE DEPOT #1'], 'Travel')
        s = self.sug('Checking', 'OFFICE DEPOT #2')
        self.assertEqual((s['account_id'], s['source']), (self.acct('Travel'), 'history'))
        first = self.rule('depot', OS)
        self.rule('office', 'Utilities')
        s = self.sug('Checking', 'OFFICE DEPOT #2')
        self.assertEqual((s['account_id'], s['source'], s['rule_id']), (self.acct(OS), 'rule', first['id']))

    def test_rule_created_while_categorizing(self):
        v = self.post('/api/vendors', {'name': 'Shell'})
        csv = ('Date,Description,Amount\n01/10/2026,SHELL OIL 5551,-45.00\n01/17/2026,SHELL OIL 7777,-50.00\n'
               '01/18/2026,SHELL OIL REBATE,5.00\n')
        self.imp('Checking', csv)
        self.cat(self.rows('Checking')['SHELL OIL 5551'], 'Travel', rule_text=' Shell Oil ', name_id=v['id'])
        rules = self.get('/api/rules')
        self.assertEqual([(r['match_text'], r['direction'], r['account'], r['name']) for r in rules],
                         [('Shell Oil', 'out', 'Travel', 'Shell')])
        self.assertEqual(self.sug('Checking', 'SHELL OIL 7777')['source'], 'rule')
        self.assertIsNone(self.sug('Checking', 'SHELL OIL REBATE'))      # rule is money-out only
        r = self.post('/api/feed/batch', {'action': 'accept', 'ids': [x['id'] for x in self.feed('Checking')['rows']]})
        self.assertEqual((r['done'], r['skipped']), (1, 1))
        posted = self.rows('Checking', 'posted')['SHELL OIL 7777']
        self.assertEqual((posted['category'], posted['name']), ('Travel', 'Shell'))
        self.assertEqual(self.plval('Travel'), 95.0)

    def test_rule_crud_and_validation(self):
        r = self.rule('uber', 'Travel', 'out')
        r2 = self.put('/api/rules/%d' % r['id'], {'match_text': 'lyft', 'direction': 'any',
                                                  'account_id': self.acct('Meals and Entertainment')})
        self.assertEqual((r2['id'], r2['match_text'], r2['direction'], r2['account']),
                         (r['id'], 'lyft', 'any', 'Meals and Entertainment'))
        self.assertEqual(len(self.get('/api/rules')), 1)
        self.post('/api/rules', {'match_text': '', 'account_id': self.acct('Travel')}, expect=400)
        self.post('/api/rules', {'match_text': 'x', 'direction': 'sideways', 'account_id': self.acct('Travel')}, expect=400)
        self.post('/api/rules', {'match_text': 'x', 'account_id': 999999}, expect=400)
        self.post('/api/rules', {'match_text': 'x'}, expect=400)
        self.post('/api/rules', {'match_text': 'x', 'account_id': self.acct('Travel'), 'name_id': 4242}, expect=400)
        self.put('/api/rules/999', {'match_text': 'x', 'account_id': self.acct('Travel')}, expect=404)
        self.assertEqual(len(self.get('/api/rules')), 1)
        self.delete('/api/rules/%d' % r['id'])
        self.assertEqual(self.get('/api/rules'), [])

    def test_rule_pointing_at_inactive_or_same_account_is_ignored(self):
        self.imp('Checking', JAN)
        self.rule('office depot', 'Checking')
        self.assertIsNone(self.sug('Checking', 'OFFICE DEPOT #123'))
        self.rule('office depot', 'Insurance')
        self.put('/api/accounts/%d' % self.acct('Insurance'), {'active': False})
        self.assertIsNone(self.sug('Checking', 'OFFICE DEPOT #123'))

    def test_history_is_direction_specific_and_ignores_splits(self):
        csv = ('Date,Description,Amount\n01/05/2026,COSTCO WHSE #1,-10.00\n01/06/2026,COSTCO WHSE #2,-20.00\n'
               '01/07/2026,COSTCO WHSE #3,5.00\n')
        self.imp('Checking', csv)
        rows = self.rows('Checking')
        self.cat(rows['COSTCO WHSE #1'], splits=[{'account_id': self.acct(OS), 'amount': 6},
                                                 {'account_id': self.acct('Travel'), 'amount': 4}])
        self.assertIsNone(self.sug('Checking', 'COSTCO WHSE #2'))
        self.post('/api/feed/%d/undo' % rows['COSTCO WHSE #1']['id'])
        self.cat(rows['COSTCO WHSE #1'], OS)
        self.assertEqual(self.sug('Checking', 'COSTCO WHSE #2')['account_id'], self.acct(OS))
        self.assertIsNone(self.sug('Checking', 'COSTCO WHSE #3'))       # money in: no history yet
        # undo removes the history
        self.post('/api/feed/%d/undo' % rows['COSTCO WHSE #1']['id'])
        self.assertIsNone(self.sug('Checking', 'COSTCO WHSE #2'))

    def test_batch_accept_with_explicit_category_exclude_and_undo(self):
        self.imp('Checking', JAN)
        ids = [r['id'] for r in self.feed('Checking')['rows']]
        r = self.post('/api/feed/batch', {'action': 'accept', 'ids': ids[:2], 'account_id': self.acct('Uncategorized Expense')})
        self.assertEqual((r['done'], r['skipped']), (2, 0))
        r = self.post('/api/feed/batch', {'action': 'exclude', 'ids': ids})
        self.assertEqual((r['done'], r['skipped']), (2, 2))
        self.assertEqual(self.feed('Checking')['counts'], {'review': 0, 'posted': 2, 'excluded': 2})
        r = self.post('/api/feed/batch', {'action': 'undo', 'ids': ids})
        self.assertEqual((r['done'], r['skipped']), (4, 0))
        self.assertEqual(self.feed('Checking')['counts'], {'review': 4, 'posted': 0, 'excluded': 0})
        self.assertEqual(self.get('/api/journals'), [])
        self.assertEqual(self.bsval('Checking'), 0.0)
        self.post('/api/feed/batch', {'action': 'nope', 'ids': ids}, expect=400)
        self.post('/api/feed/batch', {'action': 'exclude', 'ids': [987654]}, expect=404)

    # ---- defects ------------------------------------------------------------------------------------

    def test_BUG_history_key_is_only_the_bank_prefix(self):
        """History is keyed on the first three words. Wells Fargo prefixes every card purchase with
        'PURCHASE AUTHORIZED ON', so one categorization is suggested for every unrelated merchant
        (and batch 'accept' then posts them all to that category)."""
        csv = ('Date,Description,Amount\n'
               '01/05/2026,PURCHASE AUTHORIZED ON 01/04 SHELL OIL 5551 SAN JOSE CA,-45.00\n'
               '01/06/2026,PURCHASE AUTHORIZED ON 01/05 DELTA AIR LINES ATLANTA GA,-420.00\n')
        self.imp('Checking', csv)
        rows = self.feed('Checking')['rows']
        shell = [r for r in rows if 'SHELL' in r['description']][0]
        self.cat(shell, 'Repairs and Maintenance')
        delta = self.feed('Checking')['rows'][0]
        self.assertIsNone(delta['suggestion'])

    def test_BUG_history_from_invoice_match_suggests_AR_and_breaks_batch_accept(self):
        """After a deposit is matched to an invoice, the next deposit with the same description gets
        'Accounts Receivable' as its suggested category - a category the app itself refuses - and
        accepting the selection fails for every selected row."""
        c = self.post('/api/customers', {'name': 'Acme'})
        self.post('/api/invoices', {'customer_id': c['id'], 'date': '2026-01-02', 'lines': [
            {'description': 'Work', 'qty': 1, 'rate': 2000, 'account_id': self.acct('Sales')}]})
        self.rule('shell oil', 'Travel')
        self.imp('Checking', JAN + '02/06/2026,CUSTOMER DEPOSIT,750.00\n')
        rows = self.rows('Checking')
        dep = [r for r in self.feed('Checking')['rows'] if r['amount'] == 2000.0][0]
        self.post('/api/feed/%d/match' % dep['id'], {'invoice_id': [m for m in dep['matches'] if m['kind'] == 'invoice'][0]['invoice_id']})
        nxt = [r for r in self.feed('Checking')['rows'] if r['amount'] == 750.0][0]
        sug = nxt['suggestion']
        ids = [nxt['id'], rows['SHELL OIL 5551']['id']]
        status, data = self.app.handle('POST', '/api/feed/batch', {}, {'action': 'accept', 'ids': ids})
        self.assertEqual(status, 200, data)                               # must not be rejected wholesale
        self.assertTrue(sug is None or sug['account_id'] != self.acct('Accounts Receivable'), sug)

    def test_BUG_batch_accept_ignores_existing_match_and_double_counts(self):
        """The expense is already in the books (entered by hand). The statement row shows it as a match,
        but a rule also applies; 'accept' on the selection adds a second copy instead of matching
        (QuickBooks gives a found match priority over a rule)."""
        self.journal('2026-01-05', (OS, 150.25, 0), ('Checking', 0, 150.25))
        self.rule('office depot', OS)
        self.imp('Checking', JAN)
        row = self.rows('Checking')['OFFICE DEPOT #123']
        self.assertEqual(len(row['matches']), 1)
        self.post('/api/feed/batch', {'action': 'accept', 'ids': [row['id']]})
        self.assertEqual(self.plval(OS), 150.25)
        self.assertEqual(self.bsval('Checking'), -150.25)

    def test_BUG_history_uses_feed_order_not_most_recent_decision(self):
        """'Most recent category' is decided by statement-row id, not by when the user categorized."""
        csv = ('Date,Description,Amount\n01/05/2026,COMCAST CABLE 1,-10.00\n01/06/2026,COMCAST CABLE 2,-20.00\n'
               '01/07/2026,COMCAST CABLE 3,-30.00\n')
        self.imp('Checking', csv)
        rows = self.rows('Checking')
        self.cat(rows['COMCAST CABLE 3'], OS)                 # first decision (wrong one)
        self.cat(rows['COMCAST CABLE 1'], 'Utilities')        # latest decision
        self.assertEqual(self.sug('Checking', 'COMCAST CABLE 2')['account_id'], self.acct('Utilities'))


# =====================================================================================================
# Matching
# =====================================================================================================

class Matching(FeedCase):
    def invoice(self, date, amount, customer='Acme'):
        try:
            c = [x for x in self.get('/api/customers') if x['name'] == customer][0]
        except IndexError:
            c = self.post('/api/customers', {'name': customer})
        return self.post('/api/invoices', {'customer_id': c['id'], 'date': date, 'lines': [
            {'description': 'Work', 'qty': 1, 'rate': amount, 'account_id': self.acct('Sales')}]})

    def test_match_existing_entry_and_undo_keeps_ledger(self):
        j = self.journal('2026-01-04', (OS, 150.25, 0), ('Checking', 0, 150.25))
        self.imp('Checking', JAN)
        row = self.rows('Checking')['OFFICE DEPOT #123']
        self.assertEqual([m['txn_id'] for m in row['matches']], [j['id']])
        self.post('/api/feed/%d/match' % row['id'], {'line_id': row['matches'][0]['line_id']})
        self.assertEqual(self.plval(OS), 150.25)                       # not double counted
        posted = self.rows('Checking', 'posted')['OFFICE DEPOT #123']
        self.assertEqual((posted['matched'], posted['category'], posted['txn_id']), (True, OS, j['id']))
        self.post('/api/feed/%d/undo' % row['id'])
        self.assertEqual(self.plval(OS), 150.25)                       # hand-entered entry survives
        self.assertEqual(len(self.rows('Checking')['OFFICE DEPOT #123']['matches']), 1)

    def test_match_validation(self):
        j1 = self.journal('2026-01-04', (OS, 150.25, 0), ('Checking', 0, 150.25))
        j2 = self.journal('2026-01-04', (OS, 45, 0), ('Savings', 0, 45))
        self.imp('Checking', JAN + '01/05/2026,OFFICE DEPOT #123,-150.25\n')
        chk_line = [l for l in j1['lines'] if l['account'] == 'Checking'][0]['id']
        exp_line = [l for l in j1['lines'] if l['account'] == OS][0]['id']
        sav_line = [l for l in j2['lines'] if l['account'] == 'Savings'][0]['id']
        rows = self.feed('Checking')['rows']
        shell = [r for r in rows if r['description'] == 'SHELL OIL 5551'][0]
        depots = [r for r in rows if r['description'] == 'OFFICE DEPOT #123']
        self.assertEqual(len(depots), 2)
        self.assertEqual(shell['matches'], [])                                         # other account's line
        self.post('/api/feed/%d/match' % shell['id'], {'line_id': sav_line}, expect=400)   # wrong account
        self.post('/api/feed/%d/match' % shell['id'], {'line_id': chk_line}, expect=400)   # wrong amount
        self.post('/api/feed/%d/match' % depots[0]['id'], {'line_id': exp_line}, expect=400)
        self.post('/api/feed/%d/match' % depots[0]['id'], {'line_id': 99999}, expect=400)
        self.post('/api/feed/%d/match' % depots[0]['id'], {}, expect=400)
        self.post('/api/feed/%d/match' % depots[0]['id'], {'line_id': chk_line})
        self.post('/api/feed/%d/match' % depots[0]['id'], {'line_id': chk_line}, expect=409)   # already handled
        self.post('/api/feed/%d/match' % depots[1]['id'], {'line_id': chk_line}, expect=409)   # line already used
        other = [r for r in self.feed('Checking')['rows'] if r['id'] == depots[1]['id']][0]
        self.assertEqual(other['matches'], [])

    def test_sign_must_match(self):
        # a deposit of 150.25 in the books must not match a withdrawal of 150.25
        self.journal('2026-01-04', ('Checking', 150.25, 0), ('Sales', 0, 150.25))
        self.imp('Checking', JAN)
        self.assertEqual(self.rows('Checking')['OFFICE DEPOT #123']['matches'], [])

    def test_match_window(self):
        self.journal('2025-09-01', (OS, 150.25, 0), ('Checking', 0, 150.25))     # > 90 days before
        self.journal('2026-02-01', (OS, 150.25, 0), ('Checking', 0, 150.25))     # > 10 days after
        self.imp('Checking', JAN)
        self.assertEqual(self.rows('Checking')['OFFICE DEPOT #123']['matches'], [])

    def test_invoice_match_full_and_undo(self):
        inv = self.invoice('2026-01-02', 2000)
        self.imp('Checking', JAN)
        row = self.rows('Checking')['CUSTOMER DEPOSIT']
        self.assertEqual([m['invoice_id'] for m in row['matches'] if m['kind'] == 'invoice'], [inv['id']])
        t = self.post('/api/feed/%d/match' % row['id'], {'invoice_id': inv['id']})
        self.assertEqual(self.sides(t), {'Checking': 2000.0, 'Accounts Receivable': -2000.0})
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['status'], 'Paid')
        self.assertEqual(self.plval('Sales'), 2000.0)            # income counted once (from the invoice)
        self.assertEqual(self.bsval('Accounts Receivable'), 0.0)
        self.post('/api/feed/%d/undo' % row['id'])
        self.assertEqual(self.get('/api/invoices/%d' % inv['id'])['balance'], 2000.0)
        self.assertEqual(self.get('/api/payments'), [])
        self.assertEqual((self.bsval('Checking'), self.bsval('Accounts Receivable')), (0.0, 2000.0))
        self.assertEqual(self.feed('Checking')['counts']['review'], 4)
        self.assertBalanced()

    def test_invoice_match_partial_and_limits(self):
        inv = self.invoice('2026-01-02', 2500)
        small = self.invoice('2026-01-02', 100, 'Beta')
        self.imp('Checking', JAN)
        rows = self.rows('Checking')
        dep = rows['CUSTOMER DEPOSIT']
        self.assertEqual([m for m in dep['matches'] if m['kind'] == 'invoice'], [])      # no exact amount
        self.post('/api/feed/%d/match' % dep['id'], {'invoice_id': small['id']}, expect=400)   # deposit too big
        self.post('/api/feed/%d/match' % rows['SHELL OIL 5551']['id'], {'invoice_id': inv['id']}, expect=400)
        self.post('/api/feed/%d/match' % dep['id'], {'invoice_id': 9999}, expect=404)
        self.post('/api/feed/%d/match' % dep['id'], {'invoice_id': inv['id']})
        i = self.get('/api/invoices/%d' % inv['id'])
        self.assertEqual(i['balance'], 500.0)
        self.assertEqual(self.bsval('Accounts Receivable'), 600.0)

    def test_invoice_dated_after_deposit_not_offered(self):
        self.invoice('2026-01-20', 2000)
        self.imp('Checking', JAN)
        self.assertEqual(self.rows('Checking')['CUSTOMER DEPOSIT']['matches'], [])

    def test_received_payment_is_offered_as_match(self):
        """Payment already recorded straight into Checking: the deposit should match it, not the invoice."""
        inv = self.invoice('2026-01-02', 2000)
        self.post('/api/payments', {'customer_id': inv['customer_id'], 'date': '2026-01-06', 'amount': 2000,
                                    'deposit_account_id': self.acct('Checking'),
                                    'applications': [{'invoice_id': inv['id'], 'amount': 2000}]})
        self.imp('Checking', JAN)
        row = self.rows('Checking')['CUSTOMER DEPOSIT']
        self.assertEqual([m['kind'] for m in row['matches']], ['transaction'])
        self.post('/api/feed/%d/match' % row['id'], {'line_id': row['matches'][0]['line_id']})
        self.assertEqual(self.bsval('Checking'), 2000.0)


# =====================================================================================================
# State transitions and bad input
# =====================================================================================================

class States(FeedCase):
    def test_transitions(self):
        self.imp('Checking', JAN)
        rows = self.rows('Checking')
        a, b = rows['OFFICE DEPOT #123'], rows['SHELL OIL 5551']
        self.post('/api/feed/%d/exclude' % a['id'])
        self.post('/api/feed/%d/exclude' % a['id'], expect=409)
        self.cat(a, OS, expect=409)
        self.post('/api/feed/%d/match' % a['id'], {'line_id': 1}, expect=409)
        self.assertEqual(self.bsval('Checking'), 0.0)
        self.assertEqual([r['description'] for r in self.feed('Checking', 'excluded')['rows']], ['OFFICE DEPOT #123'])
        self.post('/api/feed/%d/undo' % a['id'])
        self.cat(a, OS)
        self.cat(a, OS, expect=409)                       # no double posting
        self.post('/api/feed/%d/exclude' % a['id'], expect=409)
        self.assertEqual(self.plval(OS), 150.25)
        self.post('/api/feed/%d/undo' % a['id'])
        self.post('/api/feed/%d/undo' % a['id'])          # harmless on a row already in review
        self.assertEqual(self.plval(OS), 0.0)
        self.cat(b, 'Travel')
        self.assertEqual(self.feed('Checking')['counts'], {'review': 3, 'posted': 1, 'excluded': 0})
        for verb in ('categorize', 'match', 'exclude', 'undo'):
            self.post('/api/feed/99999/%s' % verb, {'account_id': self.acct(OS)}, expect=404)

    def test_deleting_the_ledger_entry_returns_row_to_review(self):
        self.imp('Checking', JAN)
        row = self.rows('Checking')['OFFICE DEPOT #123']
        t = self.cat(row, OS)
        self.delete('/api/transactions/%d' % t['id'])
        self.assertEqual(self.feed('Checking')['counts'], {'review': 4, 'posted': 0, 'excluded': 0})
        self.cat(row, OS)
        self.assertEqual(self.plval(OS), 150.25)

    def test_feed_listing_validation_and_summary(self):
        self.imp('Checking', JAN)
        self.call('GET', '/api/feed', expect=400)
        self.call('GET', '/api/feed', expect=400, account_id=self.acct('Sales'))
        self.call('GET', '/api/feed', expect=400, account_id=self.acct('Checking'), status='bogus')
        s = {x['name']: x for x in self.get('/api/feed/summary')}
        self.assertEqual((s['Checking']['review_count'], s['Credit Card']['review_count']), (4, 0))
        rows = self.feed('Checking')['rows']
        self.assertEqual([r['date'] for r in rows], sorted((r['date'] for r in rows), reverse=True))

    def test_BUG_malformed_requests_return_500(self):
        """Bad client input should be a 4xx, never an unhandled exception."""
        self.imp('Checking', JAN)
        chk = self.acct('Checking')
        rid = self.feed('Checking')['rows'][0]['id']
        csv = 'Date,Description,Amount\n01/05/2026,X,-1.00\n'
        cases = [
            ('POST', '/api/feed/preview', {'account_id': chk, 'content_b64': 12345}),
            ('POST', '/api/feed/preview', {'account_id': chk, 'content': csv, 'filename': 123}),
            ('POST', '/api/feed/preview', {'account_id': [chk], 'content': csv}),
            ('POST', '/api/feed/preview', {'account_id': chk, 'content': ['a', 'b']}),
            ('POST', '/api/feed/preview', {'account_id': chk, 'content': 'a,b\x00c\n01/05/2026,X\x00,-1\n'}),
            ('POST', '/api/feed/%d/categorize' % rid, {'splits': 'abc'}),
            ('POST', '/api/feed/%d/categorize' % rid, {'splits': [1, 2]}),
            ('POST', '/api/feed/%d/categorize' % rid, {'splits': {'account_id': 1, 'amount': 1}}),
            ('POST', '/api/feed/%d/categorize' % rid, {'account_id': {'x': 1}}),
            ('POST', '/api/feed/%d/categorize' % rid, {'account_id': self.acct(OS), 'name_id': 'abc'}),
            ('POST', '/api/feed/%d/match' % rid, {'line_id': 'abc'}),
            ('POST', '/api/feed/%d/match' % rid, {'invoice_id': 'abc'}),
            ('POST', '/api/feed/batch', {'action': 'exclude', 'ids': 5}),
            ('POST', '/api/feed/batch', {'action': 'exclude', 'ids': 'abc'}),
            ('POST', '/api/feed/batch', {'action': 'accept', 'ids': [rid], 'account_id': 'abc'}),
            ('POST', '/api/rules', {'match_text': 123, 'account_id': self.acct(OS)}),
            ('POST', '/api/rules', {'match_text': 'x', 'account_id': self.acct(OS), 'direction': ['in']}),
            ('PUT', '/api/rules/1', {}),
        ]
        bad = []
        for method, path, body in cases:
            status, data = self.app.handle(method, path, {}, body)
            if status >= 500:
                bad.append((path, body, data))
        self.assertEqual(bad, [], '\n'.join('%s %r -> %s' % b for b in bad))


if __name__ == '__main__':
    unittest.main()
