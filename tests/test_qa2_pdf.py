"""QA round 2: built-in PDF reader (sqb/pdftext.py), PDF statement-line parser (sqb/parsers.parse_pdf_text)
and importing reviewed rows (POST /api/feed/preview, /api/feed/import with 'rows').

Fixtures tests/fixtures/qa2_*.pdf were printed by headless Chrome from HTML laid out like real
Chase / Bank of America / Wells Fargo / Amex / Capital One statements. Small synthetic PDFs are built
below with a tiny writer (FlateDecode, ASCIIHex, ASCII85, object streams, Type0 + ToUnicode, forms...).

Tests that fail expose bugs; everything else documents behaviour that works.
"""
import base64
import os
import subprocess
import sys
import textwrap
import time
import unittest
import zlib

from helpers import ApiCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqb import parsers, pdftext  # noqa: E402
from sqb.util import ApiError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, 'fixtures')


def fixture(name):
    with open(os.path.join(FIX, name), 'rb') as f:
        return f.read()


# ------------------------------------------------------------------ tiny PDF writer

HELV = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'


def stream(data, extra=b'', filt='flate'):
    if filt == 'flate':
        data, f = zlib.compress(data), b'/Filter /FlateDecode '
    elif filt == 'ahx':
        data, f = data.hex().encode() + b'>', b'/Filter /ASCIIHexDecode '
    elif filt == 'a85':
        data, f = base64.a85encode(data) + b'~>', b'/Filter /ASCII85Decode '
    elif filt == 'a85flate':
        data, f = base64.a85encode(zlib.compress(data)) + b'~>', b'/Filter [/ASCII85Decode /FlateDecode] '
    else:
        f = b''
    return b'<< ' + f + extra + b'/Length %d >>\nstream\n' % len(data) + data + b'\nendstream'


def assemble(objs, root=1, extra_trailer=b''):
    out, offsets = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n', []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % i + o + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    out += b''.join(b'%010d 00000 n \n' % o for o in offsets)
    out += b'trailer\n<< /Size %d /Root %d 0 R %s>>\nstartxref\n%d\n%%%%EOF\n' % (
        len(objs) + 1, root, extra_trailer, xref)
    return out


def build(pages, fonts=None, extra_objs=(), filt='flate', page_extra=b'', xobjects=b''):
    """pages: content bytes per page (a list of bytes = several content streams for that page).
    Objects: 1 catalog, 2 pages, fonts from 3, then content+page objects, then extra_objs."""
    fonts = fonts or {'F1': HELV}
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', None]
    nums = {}
    for name, body in fonts.items():
        objs.append(body)
        nums[name] = len(objs)
    fontdict = b' '.join(b'/%s %d 0 R' % (n.encode(), k) for n, k in nums.items())
    kids = []
    for content in pages:
        parts = content if isinstance(content, list) else [content]
        refs = []
        for c in parts:
            objs.append(stream(c, filt=filt))
            refs.append(len(objs))
        cref = b' '.join(b'%d 0 R' % n for n in refs)
        if len(refs) > 1:
            cref = b'[' + cref + b']'
        objs.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents ' + cref +
                    b' /Resources << /Font << ' + fontdict + b' >> ' + xobjects + b'>> ' + page_extra + b'>>')
        kids.append(len(objs))
    objs[1] = b'<< /Type /Pages /Kids [' + b' '.join(b'%d 0 R' % k for k in kids) + b'] /Count %d >>' % len(kids)
    objs.extend(extra_objs)
    return assemble(objs)


def esc(s):
    return s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def lines_content(lines, x=72, y=720, step=14, font='F1', size=10):
    ops = ['BT', '/%s %g Tf' % (font, size), '%g %g Td' % (x, y)]
    for ln in lines:
        ops += ['(%s) Tj' % esc(ln), '0 -%g Td' % step]
    ops.append('ET')
    return '\n'.join(ops).encode('latin-1')


def text_pdf(lines, **kw):
    return build([lines_content(lines)], **kw)


def tounicode(pairs, codespace=b'<0000> <FFFF>', ranges=()):
    body = (b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n1 begincodespacerange\n' + codespace +
            b'\nendcodespacerange\n')
    if pairs:
        body += b'%d beginbfchar\n' % len(pairs) + b''.join(b'<%s> <%s>\n' % (a, b) for a, b in pairs) + b'endbfchar\n'
    if ranges:
        body += b'%d beginbfrange\n' % len(ranges) + b''.join(r + b'\n' for r in ranges) + b'endbfrange\n'
    return body + b'endcmap CMapName currentdict /CMap defineresource pop end end'


# Type0 font: CID n+1 is printable ASCII char n (n = 0..94).
ASCII = ''.join(chr(c) for c in range(32, 127))
CID = {c: i + 1 for i, c in enumerate(ASCII)}
ASCII_PAIRS = [(b'%04X' % (i + 1), b'%04X' % ord(c)) for i, c in enumerate(ASCII)]


def cid_hex(s):
    return ('<' + ''.join('%04X' % CID[c] for c in s) + '>').encode()


def type0_objs(first_num, pairs=ASCII_PAIRS, ranges=(), w=b''):
    """[type0 font, descendant, tounicode] numbered first_num.. first_num+2."""
    return [b'<< /Type /Font /Subtype /Type0 /BaseFont /AAAAAA+Arial /Encoding /Identity-H '
            b'/DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>' % (first_num + 1, first_num + 2),
            b'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /AAAAAA+Arial /CIDSystemInfo << /Registry (Adobe) '
            b'/Ordering (Identity) /Supplement 0 >> /DW 556 ' + w + b'>>',
            stream(tounicode(pairs, ranges=ranges))]


def page_objs(content, font_refs, extra=b''):
    """catalog, pages, page, content -> objects 1..4; fonts follow from 5."""
    fd = b' '.join(b'/%s %d 0 R' % (k.encode(), v) for k, v in font_refs.items())
    return [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << ' + fd +
            b' >> ' + extra + b'>> >>', stream(content)]


def extract_in_subprocess(data, timeout=5.0):
    """Run extract_text in a child process so a hang cannot stall the suite. Returns (seconds, ok)."""
    path = os.path.join(HERE, '_qa2_tmp_%d.pdf' % os.getpid())
    with open(path, 'wb') as f:
        f.write(data)
    code = textwrap.dedent('''
        import sys; sys.path.insert(0, %r)
        from sqb import pdftext
        from sqb.util import ApiError
        try:
            pdftext.extract_text(open(%r, 'rb').read())
        except ApiError:
            pass
    ''' % (ROOT, path))
    t = time.time()
    try:
        subprocess.run([sys.executable, '-c', code], timeout=timeout, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return time.time() - t, True
    except subprocess.TimeoutExpired:
        return time.time() - t, False
    finally:
        os.remove(path)


def timed_extract(test, data, limit=5.0):
    t = time.time()
    out = pdftext.extract_text(data)
    test.assertLess(time.time() - t, limit, 'extraction took %.1fs' % (time.time() - t))
    return out


STMT = ['Statement period December 13, 2025 through January 14, 2026',
        'DEPOSITS AND ADDITIONS', '12/20 Payroll deposit 1,500.00',
        'WITHDRAWALS', '01/05 Office Depot 150.25']


# ================================================================== 1. PDF reader: what works

class PdfReaderWorks(unittest.TestCase):
    def test_stream_filters(self):
        for filt in ('flate', 'ahx', 'a85', 'a85flate', None):
            with self.subTest(filt=filt):
                self.assertEqual(pdftext.extract_text(text_pdf(STMT[2:3], filt=filt)), STMT[2])

    def test_multiple_pages_and_content_streams(self):
        pages = [lines_content(STMT[:3]), [b'BT /F1 10 Tf 72 720 Td (01/05 Office) Tj', b' ( Depot 150.25) Tj ET']]
        self.assertEqual(pdftext.extract_text(build(pages)).splitlines(),
                         STMT[:3] + ['01/05 Office Depot 150.25'])

    def test_text_positioning_operators(self):
        cs = (b'BT /F1 10 Tf 14 TL 72 720 Td (12/20 A 1.00) Tj T* (12/21 B 2.00) Tj (12/22 C 3.00) \' '
              b'0 -14 TD (12/23 D 4.00) Tj T* (12/24 E 5.00) Tj 1 0 0 1 72 600 Tm (12/25 F) Tj '
              b'1 0 0 1 400 600 Tm (6.00) Tj 2 0 (12/26 G 7.00) " ET')
        self.assertEqual(pdftext.extract_text(build([cs])).splitlines(),
                         ['12/20 A 1.00', '12/21 B 2.00', '12/22 C 3.00', '12/23 D 4.00', '12/24 E 5.00',
                          '12/25 F 6.00', '12/26 G 7.00'])

    def test_tj_kerning_and_scaled_font(self):
        cs = (b'BT /F1 10 Tf 72 720 Td [(12/20) -2000 (Pay) 30 (roll) -278 (deposit) -9000 (1,) -10 (500.00)] TJ ET '
              b'BT /F1 1 Tf 10 0 0 10 72 700 Tm (01/05 Office Depot) Tj 10 0 0 10 400 700 Tm (150.25) Tj ET')
        self.assertEqual(pdftext.extract_text(build([cs])).splitlines(),
                         ['12/20 Payroll deposit 1,500.00', '01/05 Office Depot 150.25'])

    def test_chrome_style_flipped_ctm_and_nested_q(self):
        cs = (b'1 0 0 -1 0 792 cm q q 1 0 0 1 0 0 cm BT /F1 10 Tf 1 0 0 -1 72 72 Tm (12/20 First 1.00) Tj ET Q '
              b'BT /F1 10 Tf 1 0 0 -1 72 86 Tm (12/21 Second 2.00) Tj ET Q')
        self.assertEqual(pdftext.extract_text(build([cs])).splitlines(), ['12/20 First 1.00', '12/21 Second 2.00'])

    def test_form_xobject(self):
        form = stream(b'BT /F1 10 Tf 0 0 Td (01/05 Office Depot 150.25) Tj ET',
                      extra=b'/Type /XObject /Subtype /Form /BBox [0 0 600 50] /Matrix [1 0 0 1 72 600] '
                            b'/Resources << /Font << /F1 3 0 R >> >> ')
        data = build([b'BT /F1 10 Tf 72 720 Td (12/20 Payroll 1,500.00) Tj ET /X1 Do'],
                     xobjects=b'/XObject << /X1 6 0 R >> ', extra_objs=[form])
        self.assertEqual(pdftext.extract_text(data).splitlines(), ['12/20 Payroll 1,500.00', '01/05 Office Depot 150.25'])

    def test_object_stream(self):
        inner = [(3, b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
                     b'/Resources << /Font << /F1 5 0 R >> >> >>'),
                 (5, HELV), (2, b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'), (1, b'<< /Type /Catalog /Pages 2 0 R >>')]
        hdr, body = b'', b''
        for n, o in inner:
            hdr += b'%d %d ' % (n, len(body))
            body += o + b'\n'
        objstm = stream(hdr + body, extra=b'/Type /ObjStm /N %d /First %d ' % (len(inner), len(hdr)))
        data = (b'%PDF-1.5\n4 0 obj\n' + stream(lines_content(STMT)) + b'\nendobj\n6 0 obj\n' + objstm +
                b'\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n')
        self.assertEqual(pdftext.extract_text(data).splitlines(), STMT)

    def test_type0_tounicode_bfchar_bfrange_and_ligature(self):
        # bfrange with a start value and with an array; CID 200 -> "fi" ligature U+FB01 (NFKC -> "fi")
        ranges = (b'<0060> <0062> <0058>', b'<0070> <0071> [<0051> <0052>]')
        content = (b'BT /F2 10 Tf 72 720 Td ' + cid_hex('12/20 ') + b' Tj <00C8> Tj ' + cid_hex('rst ') +
                   b' Tj <006000610062> Tj <00700071> Tj ' + cid_hex(' 1,500.00') + b' Tj ET')
        pairs = ASCII_PAIRS + [(b'00C8', b'FB01')]
        objs = page_objs(content, {'F2': 5}) + type0_objs(5, pairs=pairs, ranges=ranges)
        self.assertEqual(pdftext.extract_text(assemble(objs)), '12/20 first XYZQR 1,500.00')

    def test_winansi_non_ascii(self):
        data = build([b'BT /F1 10 Tf 72 720 Td (12/20 Caf\xe9 \x96 M\xfcnchen \x80 5.00) Tj ET'])
        self.assertEqual(pdftext.extract_text(data), '12/20 Café – München € 5.00')

    def test_encrypted_and_not_pdf_and_garbage_give_400(self):
        enc = text_pdf(STMT).replace(b'/Root 1 0 R', b'/Root 1 0 R /Encrypt 9 0 R')
        for data, msg in ((enc, 'password'), (b'hello', 'not a PDF')):
            with self.assertRaises(ApiError) as cm:
                pdftext.extract_text(data)
            self.assertEqual(cm.exception.status, 400)
            self.assertIn(msg, cm.exception.message)
        full = text_pdf(STMT)
        for data in (full[:len(full) // 2], b'%PDF-1.4\n', b'%PDF-1.4\n' + bytes(range(256)) * 50,
                     b'%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R', b'%PDF-1.4\n1 0 obj (unterminated'):
            try:
                self.assertIsInstance(timed_extract(self, data), str)
            except ApiError as e:
                self.assertEqual(e.status, 400)

    def test_scanned_image_only_pdf_is_400_with_hint(self):
        img = stream(b'\xff' * 100, extra=b'/Type /XObject /Subtype /Image /Width 10 /Height 10 '
                                          b'/BitsPerComponent 8 /ColorSpace /DeviceGray ', filt=None)
        img = img.replace(b'<< ', b'<< /Filter /DCTDecode ', 1)
        data = build([b'q 612 0 0 792 0 0 cm /Im1 Do Q'], xobjects=b'/XObject << /Im1 6 0 R >> ', extra_objs=[img])
        self.assertEqual(pdftext.extract_text(data), '')
        with self.assertRaises(ApiError) as cm:
            parsers.parse_statement(data, 'scan.pdf')
        self.assertIn('scanned', cm.exception.message)

    def test_chrome_fixtures_fast(self):
        for name in ('qa2_chase.pdf', 'qa2_bofa.pdf', 'qa2_wf.pdf', 'qa2_amex.pdf', 'qa2_capone.pdf',
                     'qa2_chasecc.pdf', 'qa2_big.pdf'):
            with self.subTest(name=name):
                self.assertTrue(timed_extract(self, fixture(name)).strip())

    def test_big_multi_page_statement(self):
        text = timed_extract(self, fixture('qa2_big.pdf'))
        rows = parsers.parse_pdf_text(text)
        self.assertEqual(len(rows), 1200)
        self.assertEqual(rows[0], {'date': '2026-01-01', 'amount': -1025, 'fitid': None,
                                   'description': 'POS PURCHASE MERCHANT 0000 SOME CITY ST REF00000000'})
        self.assertTrue(all(r['amount'] < 0 for r in rows))


# ================================================================== 2. PDF reader: bugs

class PdfReaderBugs(unittest.TestCase):
    def test_Q_restores_font(self):
        """Text state (Tf) is part of the graphics state: after Q the font set before q applies again.
        pdftext saves only the CTM on q, so text after Q is decoded with the inner Type0 font and vanishes."""
        content = (b'BT /F1 10 Tf ET q BT /F2 10 Tf 72 720 Td ' + cid_hex('12/20 Payroll deposit 1,500.00') +
                   b' Tj ET Q BT 72 700 Td (01/05 Office Depot 150.25) Tj ET')
        objs = page_objs(content, {'F1': 5, 'F2': 6}) + [HELV] + type0_objs(6)
        self.assertEqual(pdftext.extract_text(assemble(objs)).splitlines(),
                         ['12/20 Payroll deposit 1,500.00', '01/05 Office Depot 150.25'])

    def test_inline_image_does_not_swallow_following_text(self):
        """BI ... ID <binary> EI: the binary bytes are run through the tokenizer; a '[' or '(' in the image
        data swallows every later operator, so all text after a small inline logo is lost."""
        cs = (b'BT /F1 10 Tf 72 740 Td (Statement 2025) Tj ET\n'
              b'q 10 0 0 10 72 760 cm BI /W 4 /H 1 /BPC 8 /CS /G ID \x5b(\x01\x02 EI Q\n'
              b'BT /F1 10 Tf 72 720 Td (12/20 Payroll deposit 1,500.00) Tj ET')
        self.assertEqual(pdftext.extract_text(build([cs])).splitlines(),
                         ['Statement 2025', '12/20 Payroll deposit 1,500.00'])

    def test_page_tree_with_repeated_kids_does_not_hang(self):
        """A Pages node listing itself twice in /Kids recurses 2^30 times (depth guard only)."""
        objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [2 0 R 2 0 R 3 0 R] /Count 1 >>',
                b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
                b'/Resources << /Font << /F1 5 0 R >> >> >>', stream(lines_content(STMT[2:3])), HELV]
        secs, finished = extract_in_subprocess(assemble(objs))
        self.assertTrue(finished, 'extraction of a 600-byte PDF still running after %.0fs' % secs)

    def test_self_referencing_form_does_not_hang(self):
        """A 1 KB form XObject that draws itself 30 times: depth limit 5 -> 30^5 runs."""
        form = stream(b'BT /F1 10 Tf 0 0 Td (x) Tj ET ' + b'/X1 Do ' * 30,
                      extra=b'/Type /XObject /Subtype /Form /BBox [0 0 600 50] '
                            b'/Resources << /Font << /F1 3 0 R >> /XObject << /X1 6 0 R >> >> ')
        data = build([b'/X1 Do'], xobjects=b'/XObject << /X1 6 0 R >> ', extra_objs=[form])
        secs, finished = extract_in_subprocess(data)
        self.assertTrue(finished, 'extraction still running after %.0fs' % secs)

    def test_simple_font_tounicode_with_two_byte_codespace(self):
        """Some generators write <0000> <FFFF> as the codespace of a simple (single-byte) TrueType font's
        ToUnicode map. The font type decides the code length; pdftext lets the CMap switch the font to
        two-byte codes and every character is lost (empty text -> 'scanned image' error)."""
        cmap = stream(tounicode((), ranges=(b'<0020> <007E> <0020>',)))
        objs = page_objs(lines_content(STMT[2:]), {'F1': 5}) + [
            b'<< /Type /Font /Subtype /TrueType /BaseFont /Arial /FirstChar 32 /LastChar 126 '
            b'/Encoding /WinAnsiEncoding /ToUnicode 6 0 R >>', cmap]
        self.assertEqual(pdftext.extract_text(assemble(objs)).splitlines(), STMT[2:])

    def test_differences_ligature_glyph(self):
        """Simple font without ToUnicode whose /Differences maps a code to /fi: the glyph name is ignored and
        a control character comes out ('Of\\x01ce')."""
        font = (b'<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman '
                b'/Encoding << /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences [1 /fi] >> >>')
        data = build([b'BT /F1 10 Tf 72 720 Td (01/05 Of\\001ce supplies 5.00) Tj ET'], fonts={'F1': font})
        self.assertEqual(pdftext.extract_text(data), '01/05 Office supplies 5.00')

    def test_standard_font_without_widths_glyph_by_glyph(self):
        """Helvetica (standard 14, no /Widths) placed glyph by glyph: every glyph is assumed 0.5 em wide, so
        wide letters (W, M, O, N, C) get a space after them: 'W W W .AM AZO N .C O M'."""
        adv = {'W': 9.44, 'M': 8.33, 'A': 6.67, 'Z': 6.11, 'O': 7.78, 'N': 7.22, '.': 2.78, 'C': 7.22}
        ops, x = ['BT /F1 10 Tf'], 72.0
        for ch in 'WWW.AMAZON.COM':
            ops.append('1 0 0 1 %.2f 720 Tm (%s) Tj' % (x, ch))
            x += adv[ch]
        self.assertEqual(pdftext.extract_text(build(['\n'.join(ops + ['ET']).encode()])), 'WWW.AMAZON.COM')

    def test_chrome_word_gap_after_kerned_A(self):
        """Chrome draws glyph by glyph and omits the space glyph; the gap between 'DELTA' and 'AIR' is
        1.67pt at 10pt (Arial space minus kerning), under the 0.2 em threshold, so the words merge."""
        self.assertIn('DELTA AIR LINES ATLANTA GA', pdftext.extract_text(fixture('qa2_amex.pdf')))

    def test_rotated_landscape_content(self):
        """Landscape content drawn through a 90-degree cm: lines are grouped by device y, so columns become lines."""
        cs = (b'q 0 1 -1 0 612 0 cm BT /F1 10 Tf 72 500 Td (12/20 Payroll) Tj 200 0 Td (1,500.00) Tj ET '
              b'BT /F1 10 Tf 72 486 Td (12/21 Rent) Tj 200 0 Td (900.00) Tj ET Q')
        self.assertEqual(pdftext.extract_text(build([cs])).splitlines(), ['12/20 Payroll 1,500.00', '12/21 Rent 900.00'])

    def test_integer_objects_in_object_stream(self):
        """Two consecutive integer objects in an ObjStm: the first is read as the second's value
        (parse_obj keeps reading 'n n' as a possible reference), so /FirstChar 7 0 R -> 126 instead of 32."""
        inner = [(7, b'32'), (8, b'126')]
        hdr, body = b'', b''
        for n, o in inner:
            hdr += b'%d %d ' % (n, len(body))
            body += o + b'\n'
        doc = pdftext.Doc(b'%PDF-1.5\n6 0 obj\n' + stream(hdr + body, extra=b'/Type /ObjStm /N 2 /First %d ' % len(hdr))
                          + b'\nendobj\n')
        self.assertEqual((doc.get(pdftext.Ref(7)), doc.get(pdftext.Ref(8))), (32, 126))

    def test_inline_encrypt_dictionary_detected(self):
        """'/Encrypt<<' (direct dictionary, no space) is not caught by the /Encrypt\\s check, so an encrypted
        file is read as ciphertext instead of getting the 'password protected' message."""
        data = text_pdf(STMT).replace(b'/Root 1 0 R', b'/Root 1 0 R /Encrypt<</Filter /Standard /V 1 /R 2 '
                                                      b'/O <00> /U <00> /P -4>>')
        with self.assertRaises(ApiError):
            pdftext.extract_text(data)


# ================================================================== 3. statement-line parser

def rows_of(text, **kw):
    return [(r['date'], r['amount']) for r in parsers.parse_pdf_text(text, **kw)]


class StatementParserWorks(unittest.TestCase):
    def test_chase_checking_fixture(self):
        rows = parsers.parse_pdf_text(pdftext.extract_text(fixture('qa2_chase.pdf')))
        got = [(r['date'], r['amount']) for r in rows]
        for want in [('2025-12-20', 150000), ('2026-01-02', 20000), ('2026-01-05', -15025),
                     ('2026-01-09', -8999), ('2026-01-14', -1200)]:
            self.assertIn(want, got)
        # summary table and daily-ending-balance table are not transactions
        self.assertFalse([r for r in rows if r['amount'] in (170000, 219776, -219776, 250000, 229975, -229975)])
        self.assertNotIn('Sec:PPD', ' '.join(r['description'] for r in rows))

    def test_bofa_fixture_full_year_dates_and_sections(self):
        got = rows_of(pdftext.extract_text(fixture('qa2_bofa.pdf')))
        for want in [('2025-12-01', 300000), ('2025-12-03', -23456), ('2025-12-15', -100000)]:
            self.assertIn(want, got)
        self.assertFalse([d for d, a in got if not d.startswith('2025-12')])

    def test_chase_card_fixture(self):
        self.assertEqual(rows_of(pdftext.extract_text(fixture('qa2_chasecc.pdf')), is_cc=True),
                         [('2025-12-05', 50000), ('2025-12-01', -6420), ('2025-12-24', -1200)])

    def test_running_balance_cr_and_parentheses(self):
        text = ('Beginning balance $1,000.00\n12/02 Deposit 100.00 1,100.00\n12/03 Debit card 40.00 1,060.00\n'
                '12/04 Refund 5.00 CR\n12/05 Fee (2.50)\n12/06 Reversal 7.00-\nStatement 2025')
        self.assertEqual(rows_of(text), [('2025-12-02', 10000), ('2025-12-03', -4000), ('2025-12-04', 500),
                                         ('2025-12-05', -250), ('2025-12-06', -700)])

    def test_new_year_span_numeric(self):
        text = 'Period December 13, 2025 through January 14, 2026\nPURCHASES\n12/30 A 1.00\n01/02 B 2.00'
        self.assertEqual(rows_of(text, is_cc=True), [('2025-12-30', -100), ('2026-01-02', -200)])

    def test_explicit_year_wins(self):
        self.assertEqual(rows_of('Statement 2026\n12/20 Payroll 1,500.00', default_year=2024),
                         [('2024-12-20', 150000)])


class StatementParserBugs(unittest.TestCase):
    def test_next_year_due_date_does_not_move_december_statement(self):
        """Card statement for Nov 29 - Dec 28, 2025 that prints the payment due date in January 2026.
        Any mention of the following year sets the year to 2026, and the January check only looks at
        transaction dates, so every December charge is dated 2026-12-xx (a year in the future)."""
        text = ('Opening/Closing Date 11/29/2025 - 12/28/2025\nPayment Due Date 01/22/2026\n'
                'PURCHASE\n12/05 STARBUCKS 5.25\n12/10 AMAZON 45.67')
        self.assertEqual(rows_of(text, is_cc=True), [('2025-12-05', -525), ('2025-12-10', -4567)])

    def test_bank_footer_with_next_year(self):
        """Same with a bank statement: '(c) 2026' / 'new fees effective January 1, 2026' in the footer."""
        text = ('Statement period 12/01/2025 - 12/31/2025\nDEPOSITS AND ADDITIONS\n12/20 Payroll 1,500.00\n'
                'Your new fee schedule is effective January 1, 2026')
        self.assertEqual(rows_of(text), [('2025-12-20', 150000)])

    def test_wells_fargo_daily_balance_only_on_last_row_of_day(self):
        """Wells Fargo prints 'Ending daily balance' only on the last transaction of a day. A row without a
        balance keeps its printed (positive) sign, and the next row's balance no longer equals
        previous + amount, so it falls to 'single table: assume money out'. Coffee becomes a deposit and
        payroll a withdrawal."""
        got = rows_of(pdftext.extract_text(fixture('qa2_wf.pdf')))
        self.assertEqual(got, [('2025-11-17', -525), ('2025-11-17', 150000), ('2025-11-20', -28000)])

    def test_wrapped_description_line_is_not_a_section_heading(self):
        """A wrapped second description line with no digits is taken as a section heading when it
        contains 'fee', 'payment', 'deposit'... Here 'Security deposit' (2nd line of a car-rental charge)
        switches the Amex 'New Charges' section to credits, so the next charge becomes a credit."""
        text = ('Statement 2025\nNew Charges\n12/12/25 AVIS RENT A CAR BOSTON MA $300.00\nSecurity deposit\n'
                '12/14/25 UBER TRIP HELP.UBER.COM CA $25.00')
        self.assertEqual(rows_of(text, is_cc=True), [('2025-12-12', -30000), ('2025-12-14', -2500)])

    def test_wrapped_line_in_deposits(self):
        text = 'DEPOSITS AND ADDITIONS\n12/20 Refund 35.00\nReversal of service fee\n12/22 Payroll 1,500.00\nStatement 2025'
        self.assertEqual(rows_of(text), [('2025-12-20', 3500), ('2025-12-22', 150000)])

    def test_chase_checks_paid_rows(self):
        """Chase 'CHECKS PAID' lines start with the check number ('1001 ^ 12/22 $250.00'); the line regex
        needs a leading date, so every paper check is silently dropped."""
        got = rows_of(pdftext.extract_text(fixture('qa2_chase.pdf')))
        self.assertIn(('2025-12-22', -25000), got)

    def test_bofa_two_column_checks(self):
        """BofA lists checks two per line: '12/22/25 1001 -250.00 12/29/25 1003* -75.00'. One row comes out,
        dated 12/22 with amount -75.00 and description '1001 -250.00 12/29/25 1003*'; check 1001 is lost."""
        got = rows_of(pdftext.extract_text(fixture('qa2_bofa.pdf')))
        self.assertIn(('2025-12-22', -25000), got)
        self.assertIn(('2025-12-29', -7500), got)

    def test_amex_date_with_asterisk(self):
        """Amex marks some dates with '*' ('12/10/25* AUTOPAY PAYMENT RECEIVED - THANK YOU -$1,000.00');
        the date must be followed by whitespace, so the payment is dropped."""
        got = rows_of(pdftext.extract_text(fixture('qa2_amex.pdf')), is_cc=True)
        self.assertIn(('2025-12-10', 100000), got)
        self.assertEqual(sorted(got), sorted([('2025-12-10', 100000), ('2025-12-18', 5000), ('2025-12-12', -2500),
                                              ('2025-12-30', -20067), ('2026-01-02', -2000)]))

    def test_capital_one_month_name_dates(self):
        """Capital One / Discover / US Bank print 'Dec 5' style dates: no rows at all are found."""
        got = rows_of(pdftext.extract_text(fixture('qa2_capone.pdf')), is_cc=True)
        self.assertEqual(got, [('2025-12-05', 50000), ('2025-12-01', -4567), ('2025-12-20', -3810)])


# ================================================================== 4. preview / import of reviewed rows

class FeedRowsImport(ApiCase):
    def setUp(self):
        super().setUp()
        self.chk = self.acct('Checking')
        self.cc = self.acct('Credit Card')

    def b64(self, data):
        return base64.b64encode(data).decode()

    def preview_pdf(self, data, acct=None, expect=200, **kw):
        body = {'account_id': acct or self.chk, 'filename': 's.pdf', 'content_b64': self.b64(data)}
        body.update(kw)
        return self.post('/api/feed/preview', body, expect=expect)

    def row(self, **kw):
        r = {'date': '2026-01-05', 'description': 'Office Depot', 'amount': -150.25}
        r.update(kw)
        return r

    def test_validation_is_400(self):
        bad = [
            {'rows': 'x'}, {'rows': {'a': 1}}, {'rows': []}, {'rows': [1]}, {'rows': [None]}, {'rows': [[1]]},
            {'rows': [self.row(amount='abc')]}, {'rows': [self.row(amount=[1])]}, {'rows': [self.row(amount=True)]},
            {'rows': [self.row(amount={'a': 1})]}, {'rows': [self.row(amount='1e999')]},
            {'rows': [self.row(amount=1e20)]}, {'rows': [self.row(amount='NaN')]}, {'rows': [self.row(amount=0)]},
            {'rows': [self.row(amount='')]}, {'rows': [self.row(amount=None)]},
            {'rows': [self.row(date='01/05/2026')]}, {'rows': [self.row(date='2026-02-30')]},
            {'rows': [self.row(date=20260105)]}, {'rows': [self.row(date=None)]}, {'rows': [self.row(date='1899-12-31')]},
            {'rows': [self.row(date=['2026-01-05'])]},
            {'rows': [self.row(description=['x'])]}, {'rows': [self.row(description={'a': 1})]},
            {'rows': [self.row(fitid=['x'])]}, {'rows': [self.row(), self.row(date='bad')]},
            {'rows': [self.row()], 'format': ['x']},
        ]
        for extra in bad:
            for path in ('/api/feed/preview', '/api/feed/import'):
                with self.subTest(body=str(extra)[:80], path=path):
                    body = {'account_id': self.chk}
                    body.update(extra)
                    self.assertIn('error', self.post(path, body, expect=400))
        self.post('/api/feed/import', {'account_id': self.acct('Sales'), 'rows': [self.row()]}, expect=400)
        self.post('/api/feed/import', {'account_id': 'x', 'rows': [self.row()]}, expect=400)
        self.assertEqual(self.get('/api/feed', account_id=self.chk)['rows'], [])   # nothing half-imported

    def test_accepted_row_shapes(self):
        rows = [self.row(amount='1,234.56', description=5), self.row(amount='$-5', description=None),
                self.row(amount='(7.00)', description='x' * 5000, fitid=12), self.row(amount=0),
                self.row(amount=-0.004)]
        pv = self.post('/api/feed/preview', {'account_id': self.chk, 'rows': rows})
        self.assertEqual([(r['amount'], len(r['description'])) for r in pv['rows']], [(1234.56, 1), (-5.0, 0), (-7.0, 300)])
        self.assertEqual(pv['rows'][2]['fitid'], '12')
        r = self.post('/api/feed/import', {'account_id': self.chk, 'rows': rows})
        self.assertEqual(r['imported'], 3)

    def test_edited_rows_round_trip_and_reimport_is_duplicate(self):
        pv = self.preview_pdf(fixture('chrome_statement.pdf'))
        rows = pv['rows']
        rows[1]['amount'] = -150.52
        rows[1]['description'] = 'Office Depot #123 (fixed)'
        rows[2]['date'] = '2026-01-10'
        del rows[0]
        self.assertEqual(self.post('/api/feed/import', {'account_id': self.chk, 'filename': 's.pdf', 'rows': rows})['imported'], 2)
        feed = {(x['date'], x['amount'], x['description']) for x in self.get('/api/feed', account_id=self.chk)['rows']}
        self.assertEqual(feed, {('2026-01-05', -150.52, 'Office Depot #123 (fixed)'), ('2026-01-10', -500.0, 'Check 1041')})
        again = self.post('/api/feed/preview', {'account_id': self.chk, 'rows': rows})
        self.assertEqual(again['duplicate_count'], 2)
        r = self.post('/api/feed/import', {'account_id': self.chk, 'rows': rows})
        self.assertEqual((r['imported'], r['duplicates']), (0, 2))
        # case / whitespace edits still match the stored line
        r = self.post('/api/feed/import', {'account_id': self.chk, 'rows': [
            dict(rows[0], description='  office   depot #123 (FIXED) ')]})
        self.assertEqual(r['imported'], 0)

    def test_identical_rows_same_day_counted_once_each(self):
        two = [self.row(description='Coffee', amount=-5), self.row(description='Coffee', amount=-5)]
        self.assertEqual(self.post('/api/feed/import', {'account_id': self.chk, 'rows': two})['imported'], 2)
        three = two + [self.row(description='Coffee', amount=-5)]
        r = self.post('/api/feed/import', {'account_id': self.chk, 'rows': three})
        self.assertEqual((r['imported'], r['duplicates']), (1, 2))

    def test_fitid_duplicate(self):
        self.post('/api/feed/import', {'account_id': self.chk, 'rows': [self.row(fitid='ABC1')]})
        r = self.post('/api/feed/import', {'account_id': self.chk, 'rows': [self.row(fitid='ABC1', amount=-1)]})
        self.assertEqual(r['duplicates'], 1)
        # other account: not a duplicate
        r = self.post('/api/feed/import', {'account_id': self.cc, 'rows': [self.row(fitid='ABC1')]})
        self.assertEqual(r['imported'], 1)

    def test_csv_then_same_statement_as_pdf(self):
        csv = ('Date,Description,Amount\n12/20/2025,Payroll deposit ACME,1500.00\n'
               '01/05/2026,OFFICE DEPOT #123,-150.25\n01/09/2026,Check 1041,-500.00\n')
        self.assertEqual(self.post('/api/feed/import', {'account_id': self.chk, 'content': csv})['imported'], 3)
        pv = self.preview_pdf(fixture('chrome_statement.pdf'))
        self.assertEqual((pv['count'], pv['duplicate_count']), (3, 3))
        r = self.post('/api/feed/import', {'account_id': self.chk, 'filename': 's.pdf', 'rows': pv['rows']})
        self.assertEqual(r['imported'], 0)

    def test_pdf_preview_card_account(self):
        pv = self.preview_pdf(fixture('qa2_chasecc.pdf'), acct=self.cc)
        self.assertEqual([(r['date'], r['amount']) for r in pv['rows']],
                         [('2025-12-05', 500.0), ('2025-12-01', -64.2), ('2025-12-24', -12.0)])
        self.assertEqual(pv['format'], 'pdf')

    def test_pdf_errors_are_400(self):
        enc = text_pdf(STMT).replace(b'/Root 1 0 R', b'/Root 1 0 R /Encrypt 9 0 R')
        for data in (enc, b'%PDF-1.4\n', text_pdf(['No transactions here'])):
            self.preview_pdf(data, expect=400)
        self.post('/api/feed/preview', {'account_id': self.chk, 'filename': 's.pdf', 'content_b64': '!!!='}, expect=400)

    def test_huge_year_is_400_not_500(self):
        """year passes as_int (<= 2**53) but datetime.date(year, m, d) raises OverflowError, which
        parse_any_date does not catch (it only catches ValueError) -> 500 'Server error'."""
        self.preview_pdf(fixture('chrome_statement.pdf'), year=2 ** 40, expect=400)

    def test_year_out_of_range_rejected(self):
        """year=1 gives '0001-12-20' rows that import fine, while reviewed rows reject years before 1900."""
        self.preview_pdf(fixture('chrome_statement.pdf'), year=1, expect=400)


if __name__ == '__main__':
    unittest.main()
