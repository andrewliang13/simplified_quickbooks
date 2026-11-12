"""Minimal PDF text extraction (standard library only) for bank and card statements.

Handles what statement PDFs normally use: compressed (Flate) content streams and object streams, ASCII hex /
ASCII85 filters, simple and composite (Type0) fonts with ToUnicode maps, and the usual text operators. Text is
put back into lines by position on the page. Scanned (image-only) PDFs have no text and give an empty result.
"""
import base64
import re
import unicodedata
import zlib

from .util import ApiError

WS = b' \t\r\n\x0c\x00'
DELIM = b'()<>[]{}/%'


class Ref:
    __slots__ = ('num',)

    def __init__(self, num):
        self.num = num


class Name(str):
    pass


class Op(str):
    pass


# ---------------------------------------------------------------- tokenizer / object parser

def _lit_string(data, i):
    """data[i] == '('; returns (bytes, next index)."""
    out, depth, i = bytearray(), 1, i + 1
    esc = {ord('n'): 10, ord('r'): 13, ord('t'): 9, ord('b'): 8, ord('f'): 12}
    while i < len(data):
        c = data[i]
        if c == 0x5C:                      # backslash
            i += 1
            if i >= len(data):
                break
            c = data[i]
            if c in esc:
                out.append(esc[c])
            elif 0x30 <= c <= 0x37:
                j = i
                while j < len(data) and j < i + 3 and 0x30 <= data[j] <= 0x37:
                    j += 1
                out.append(int(data[i:j], 8) & 0xFF)
                i = j - 1
            elif c in (10, 13):            # line continuation
                if c == 13 and i + 1 < len(data) and data[i + 1] == 10:
                    i += 1
            else:
                out.append(c)
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out.append(c)
        else:
            out.append(c)
        i += 1
    return bytes(out), i


def tokens(data, i=0, end=None):
    end = len(data) if end is None else end
    while i < end:
        c = data[i]
        if c in WS:
            i += 1
        elif c == 0x25:                    # % comment
            while i < end and data[i] not in b'\r\n':
                i += 1
        elif c == 0x28:
            s, i = _lit_string(data, i)
            yield s, i
        elif c == 0x3C:
            if data[i + 1:i + 2] == b'<':
                yield Op('<<'), i + 2
                i += 2
            else:
                j = data.find(b'>', i)
                j = end if j < 0 else j
                h = re.sub(rb'[^0-9A-Fa-f]', b'', data[i + 1:j])
                if len(h) % 2:
                    h += b'0'
                yield bytes.fromhex(h.decode()), j + 1
                i = j + 1
        elif c == 0x3E:
            yield Op('>>'), i + 2
            i += 2
        elif c in (0x5B, 0x5D, 0x7B, 0x7D):
            yield Op(chr(c)), i + 1
            i += 1
        elif c == 0x2F:
            j = i + 1
            while j < end and data[j] not in WS and data[j] not in DELIM:
                j += 1
            nm = re.sub(rb'#([0-9A-Fa-f]{2})', lambda m: bytes([int(m.group(1), 16)]), data[i + 1:j])
            yield Name(nm.decode('latin-1')), j
            i = j
        else:
            j = i
            while j < end and data[j] not in WS and data[j] not in DELIM:
                j += 1
            if j == i:
                j = i + 1
            w = data[i:j].decode('latin-1')
            if w == 'ID':                      # inline image data: skip to the EI that ends it
                k = re.compile(rb'\sEI(?=[\s]|$)').search(data, j + 1)
                i = k.end() if k else end
                yield Op('EI'), i
                continue
            try:
                yield (float(w) if any(ch in w for ch in '.eE') and not w.isalpha() else int(w)), j
            except ValueError:
                yield Op(w), j
            i = j


def parse_obj(data, i=0, end=None):
    """Parse one PDF object starting at i. Returns (object, next index)."""
    stack = [[]]
    for tok, j in tokens(data, i, end):
        if isinstance(tok, Op):
            if tok in ('[', '<<'):
                stack.append([tok])
                continue
            if tok in (']', '>>'):
                items = stack.pop()
                opener, body = items[0], items[1:]
                val = body if opener == '[' else {body[k]: body[k + 1] for k in range(0, len(body) - 1, 2)
                                                  if isinstance(body[k], Name)}
                stack[-1].append(val)
            elif tok == 'R' and len(stack[-1]) >= 2 and isinstance(stack[-1][-1], int) \
                    and isinstance(stack[-1][-2], int):
                gen = stack[-1].pop()  # noqa: F841
                stack[-1].append(Ref(stack[-1].pop()))
            elif tok in ('true', 'false'):
                stack[-1].append(tok == 'true')
            elif tok == 'null':
                stack[-1].append(None)
            else:
                stack[-1].append(tok)
        else:
            stack[-1].append(tok)
        if len(stack) == 1 and stack[0]:
            nxt = data[j:j + 12].lstrip(WS)
            # an integer may be the start of an "n g R" reference
            if isinstance(stack[0][-1], int) and re.match(rb'\d+\s+R\b', nxt):
                continue
            if isinstance(stack[0][-1], int) and len(stack[0]) == 1 and re.match(rb'\d+\s', nxt):
                continue
            return stack[0][-1], j
    return (stack[0][-1] if stack[0] else None), len(data)


# ---------------------------------------------------------------- document

class Doc:
    def __init__(self, data):
        self.data = data
        self.objs = {}                     # num -> (dict_or_value, stream_bytes or None)
        self._load()

    def _load(self):
        d = self.data
        for m in re.finditer(rb'(\d+)\s+(\d+)\s+obj\b', d):
            num = int(m.group(1))
            try:
                val, j = parse_obj(d, m.end())
            except Exception:
                continue
            stream = None
            rest = d[j:j + 20].lstrip(WS)
            if isinstance(val, dict) and rest.startswith(b'stream'):
                s = d.index(b'stream', j) + 6
                if d[s:s + 2] == b'\r\n':
                    s += 2
                elif d[s:s + 1] in (b'\n', b'\r'):
                    s += 1
                length = val.get('Length')
                e = s + length if isinstance(length, int) and d[s + length:s + length + 20].lstrip(WS).startswith(
                    b'endstream') else d.find(b'endstream', s)
                stream = d[s:e if e > 0 else len(d)]
            self.objs[num] = (val, stream)
        # objects packed inside object streams (PDF 1.5+)
        for num, (val, stream) in list(self.objs.items()):
            if isinstance(val, dict) and val.get('Type') == 'ObjStm' and stream is not None:
                try:
                    body = self.decode(val, stream)
                    n, first = int(val.get('N', 0)), int(val.get('First', 0))
                    head = [int(x) for x in body[:first].split()]
                    for k in range(n):
                        onum, off = head[2 * k], head[2 * k + 1]
                        stop = first + head[2 * k + 3] if k + 1 < n else len(body)
                        if onum not in self.objs:
                            self.objs[onum] = (parse_obj(body, first + off, stop)[0], None)
                except Exception:
                    continue

    def get(self, v):
        seen = 0
        while isinstance(v, Ref) and seen < 50:
            v = self.objs.get(v.num, (None, None))[0]
            seen += 1
        return v

    def stream_of(self, v):
        if isinstance(v, Ref) and v.num in self.objs:
            val, s = self.objs[v.num]
            return self.decode(val, s) if s is not None else b''
        return b''

    def decode(self, d, s):
        filters = self.get(d.get('Filter'))
        filters = filters if isinstance(filters, list) else ([filters] if filters else [])
        for f in filters:
            f = self.get(f)
            if f in ('FlateDecode', 'Fl'):
                try:
                    s = zlib.decompress(s)
                except zlib.error:
                    s = zlib.decompressobj().decompress(s)
            elif f in ('ASCIIHexDecode', 'AHx'):
                h = re.sub(rb'[^0-9A-Fa-f]', b'', s.split(b'>')[0])
                s = bytes.fromhex((h + b'0' * (len(h) % 2)).decode())
            elif f in ('ASCII85Decode', 'A85'):
                s = base64.a85decode(s.strip().replace(b'<~', b'').split(b'~>')[0])
            else:
                return b''                 # image or unsupported filter: no text here
        return s

    def pages(self):
        root = None
        for val, _ in self.objs.values():
            if isinstance(val, dict) and val.get('Type') == 'Catalog':
                root = val
                break
        out = []

        seen = set()

        def walk(node, inherited, depth=0):
            if isinstance(node, Ref):
                if node.num in seen:
                    return                     # a page tree that lists a node twice (or itself)
                seen.add(node.num)
            node = self.get(node)
            if not isinstance(node, dict) or depth > 30 or len(out) > 5000:
                return
            res = node.get('Resources', inherited)
            if node.get('Type') == 'Pages' or 'Kids' in node:
                for k in self.get(node.get('Kids')) or []:
                    walk(k, res, depth + 1)
            else:
                out.append((node, self.get(res)))
        if root:
            walk(root.get('Pages'), None)
        if not out:   # no usable page tree: take page objects in file order
            out = [(v, self.get(v.get('Resources'))) for v, _ in (self.objs[k] for k in sorted(self.objs))
                   if isinstance(v, dict) and v.get('Type') == 'Page']
        return out


# ---------------------------------------------------------------- fonts

# Glyph widths of Helvetica / Arial (1/1000 em) for codes 32-126, used for standard fonts without /Widths.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
    [278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
     667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
     278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
     260, 334, 584]
_GLYPHS = {'space': ' ', 'fi': 'fi', 'fl': 'fl', 'ff': 'ff', 'ffi': 'ffi', 'ffl': 'ffl', 'period': '.', 'comma': ',',
           'hyphen': '-', 'minus': '-', 'endash': '\u2013', 'emdash': '\u2014', 'quoteright': "'", 'quoteleft': "'",
           'quotedbl': '"', 'quotesingle': "'", 'dollar': '$', 'percent': '%', 'ampersand': '&', 'slash': '/',
           'colon': ':', 'semicolon': ';', 'parenleft': '(', 'parenright': ')', 'numbersign': '#', 'asterisk': '*',
           'at': '@', 'plus': '+', 'equal': '=', 'underscore': '_', 'zero': '0', 'one': '1', 'two': '2', 'three': '3',
           'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'bullet': '\u2022',
           'Euro': '\u20ac', 'sterling': '\u00a3', 'yen': '\u00a5', 'cent': '\u00a2', 'degree': '\u00b0'}


def _glyph(name):
    if len(name) == 1:
        return name
    if name in _GLYPHS:
        return _GLYPHS[name]
    m = re.fullmatch(r'uni([0-9A-Fa-f]{4})', name) or re.fullmatch(r'u([0-9A-Fa-f]{4,6})', name)
    if m:
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return ''
    return None


class Font:
    def __init__(self, doc, fd):
        fd = doc.get(fd) or {}
        self.type0 = self.two_byte = fd.get('Subtype') == 'Type0'
        self.map = {}
        self.diff = {}
        self.widths, self.default_w = {}, 500.0
        base = str(doc.get(fd.get('BaseFont')) or '')
        enc = doc.get(fd.get('Encoding'))
        if isinstance(enc, dict):
            code = 0
            for x in doc.get(enc.get('Differences')) or []:
                x = doc.get(x)
                if isinstance(x, (int, float)):
                    code = int(x)
                elif isinstance(x, Name):
                    g = _glyph(str(x))
                    if g is not None:
                        self.diff[code] = g
                    code += 1
        if not self.type0 and not fd.get('Widths'):
            if 'Courier' in base:
                self.default_w = 600.0
            else:
                self.widths = {32 + k: float(w) for k, w in enumerate(_HELV)}
                self.default_w = 556.0
        if self.two_byte:
            desc = doc.get((doc.get(fd.get('DescendantFonts')) or [None])[0]) or {}
            self.default_w = float(doc.get(desc.get('DW')) or 1000)
            w = doc.get(desc.get('W')) or []
            i = 0
            while i < len(w):
                c0, nxt = doc.get(w[i]), doc.get(w[i + 1]) if i + 1 < len(w) else None
                if isinstance(nxt, list):
                    for k, x in enumerate(nxt):
                        x = doc.get(x)
                        if isinstance(x, (int, float)):
                            self.widths[c0 + k] = float(x)
                    i += 2
                elif i + 2 < len(w):
                    if 0 <= int(nxt) - int(c0) <= 65536:
                        for c in range(int(c0), int(nxt) + 1):
                            self.widths[c] = float(doc.get(w[i + 2]))
                    i += 3
                else:
                    break
        else:
            first = doc.get(fd.get('FirstChar')) or 0
            for k, x in enumerate(doc.get(fd.get('Widths')) or []):
                x = doc.get(x)
                if isinstance(x, (int, float)):
                    self.widths[int(first) + k] = float(x)
            desc = doc.get(fd.get('FontDescriptor')) or {}
            mw = doc.get(desc.get('MissingWidth'))
            if isinstance(mw, (int, float)) and mw:
                self.default_w = float(mw)
        tu = fd.get('ToUnicode')
        if isinstance(tu, Ref):
            self._cmap(doc.stream_of(tu))

    def _cmap(self, data):
        def hexval(b):
            return int(b, 16)

        def uni(b):
            try:
                return bytes.fromhex(b.decode()).decode('utf-16-be', 'ignore')
            except ValueError:
                return ''
        for m in re.finditer(rb'begincodespacerange(.*?)endcodespacerange', data, re.S):
            lens = [len(x) // 2 for x in re.findall(rb'<([0-9A-Fa-f]+)>', m.group(1))]
            if lens and self.type0:            # only composite fonts use multi-byte codes
                self.two_byte = max(lens) >= 2
        for m in re.finditer(rb'beginbfchar(.*?)endbfchar', data, re.S):
            for a, b in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>', m.group(1)):
                self.map[hexval(a)] = uni(b)
        for m in re.finditer(rb'beginbfrange(.*?)endbfrange', data, re.S):
            for a, b, rest in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])',
                                         m.group(1)):
                lo, hi = hexval(a), hexval(b)
                if hi - lo > 65535:
                    continue
                if rest.startswith(b'['):
                    for k, x in enumerate(re.findall(rb'<([0-9A-Fa-f]*)>', rest)):
                        self.map[lo + k] = uni(x)
                else:
                    start = rest[1:-1]
                    base = hexval(start) if start else 0
                    for k in range(hi - lo + 1):
                        self.map[lo + k] = uni(format(base + k, '0%dX' % len(start)).encode())

    def codes(self, s):
        if self.two_byte:
            return [(s[i] << 8) | (s[i + 1] if i + 1 < len(s) else 0) for i in range(0, len(s), 2)]
        return list(s)

    def char(self, c):
        if c in self.map:
            return self.map[c]
        if c in self.diff:
            return self.diff[c]
        if self.map:
            return '' if self.two_byte else bytes([c & 0xFF]).decode('cp1252', 'replace')
        if self.two_byte:
            return chr(c) if 32 <= c < 0x110000 else ''
        return bytes([c]).decode('cp1252', 'replace')

    def width(self, c):
        return self.widths.get(c, self.default_w) / 1000.0


def _mul(m, n):
    """m x n for PDF matrices [a b c d e f]."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F]


def _page_text(doc, page, res):
    pieces = []                            # (y, x_start, x_end, seq, text, size)
    _run(doc, _content(doc, page.get('Contents')), res, [1, 0, 0, 1, 0, 0], pieces, 0)
    lines = {}
    for y, x0, x1, s, t, sz in pieces:
        key = next((k for k in lines if abs(k - y) <= max(1.5, sz * 0.35)), y)
        lines.setdefault(key, []).append((x0, x1, s, t, sz))
    out = []
    for y in sorted(lines, reverse=True):
        row = sorted(lines[y], key=lambda p: (round(p[0], 1), p[2]))
        text, end = '', None
        for x0, x1, s, t, sz in row:
            if end is not None and x0 - end > sz * 0.1 and not text.endswith(' ') and not t.startswith(' '):
                text += ' '
            text += t
            end = max(end or x1, x1)
        out.append(re.sub(r'[ \t]+', ' ', unicodedata.normalize('NFKC', text)).strip())
    return '\n'.join(l for l in out if l)


def _content(doc, contents):
    contents = doc.get(contents) if not isinstance(contents, Ref) or not doc.objs.get(contents.num, (0, None))[1] \
        else contents
    parts = contents if isinstance(contents, list) else [contents]
    return b'\n'.join(doc.stream_of(p) if isinstance(p, Ref) else b'' for p in parts)


def _run(doc, data, res, ctm, pieces, depth, stack_refs=frozenset(), budget=None):
    budget = budget if budget is not None else [500]     # form drawings per page
    res = doc.get(res) or {}
    fonts_dict = doc.get(res.get('Font')) or {}
    xobjects = doc.get(res.get('XObject')) or {}
    fonts = {}
    gs = []
    tm, tlm = [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, 0]
    st = {'font': None, 'size': 10.0, 'leading': 0.0, 'tc': 0.0, 'tw': 0.0, 'th': 1.0}
    operands, stack = [], []

    def font():
        f = st['font']
        if f not in fonts:
            fonts[f] = Font(doc, fonts_dict.get(f)) if f is not None else None
        return fonts[f]

    def show(items):
        f = font()
        m = _mul(tm, ctm)
        x0, y0 = m[4], m[5]
        scale = abs(m[0] * m[3] - m[1] * m[2]) ** 0.5 or 1
        text = ''
        for it in items:
            if isinstance(it, (int, float)):
                adv = -it / 1000.0 * st['size'] * st['th']
                if it < -180 and text and not text.endswith(' '):
                    text += ' '
                tm[4] += adv * tm[0]
                tm[5] += adv * tm[1]
                continue
            codes = f.codes(it) if f else list(it)
            for c in codes:
                ch = f.char(c) if f else chr(c)
                text += ch
                w = (f.width(c) if f else 0.5) * st['size'] + st['tc'] + (st['tw'] if c == 32 else 0)
                adv = w * st['th']
                tm[4] += adv * tm[0]
                tm[5] += adv * tm[1]
        e = _mul(tm, ctm)
        dx, dy = m[0], m[1]
        n = (dx * dx + dy * dy) ** 0.5 or 1
        dx, dy = dx / n, dy / n
        along0, along1 = x0 * dx + y0 * dy, e[4] * dx + e[5] * dy
        across = -x0 * dy + y0 * dx
        if text.strip():
            pieces.append((across, min(along0, along1), max(along0, along1), len(pieces), text, st['size'] * scale))

    def next_line(tx, ty):
        tlm[:] = _mul([1, 0, 0, 1, tx, ty], tlm)
        tm[:] = tlm

    for tok, _ in tokens(data):
        if isinstance(tok, Op) and tok == '[':
            stack.append([])
            continue
        if isinstance(tok, Op) and tok == ']':
            arr = stack.pop() if stack else []
            (stack[-1] if stack else operands).append(arr)
            continue
        if isinstance(tok, Op) and tok in ('<<', '>>'):
            continue
        if stack:
            stack[-1].append(tok)
            continue
        if not isinstance(tok, Op):
            operands.append(tok)
            continue
        op, a = str(tok), operands
        operands = []
        nums = [float(x) for x in a if isinstance(x, (int, float))]
        try:
            if op == 'q':
                gs.append((ctm[:], dict(st)))
            elif op == 'Q':
                if gs:
                    ctm, saved = gs.pop()
                    st.update(saved)
            elif op == 'cm' and len(nums) >= 6:
                ctm = _mul(nums[:6], ctm)
            elif op == 'BT':
                tm[:] = [1, 0, 0, 1, 0, 0]
                tlm[:] = tm
            elif op == 'Tf' and len(a) >= 2:
                st['font'] = a[0]
                if isinstance(a[1], (int, float)):
                    st['size'] = float(a[1])
            elif op == 'Tc' and nums:
                st['tc'] = nums[0]
            elif op == 'Tw' and nums:
                st['tw'] = nums[0]
            elif op == 'Tz' and nums:
                st['th'] = nums[0] / 100.0
            elif op == 'TL' and nums:
                st['leading'] = nums[0]
            elif op == 'Td' and len(nums) >= 2:
                next_line(nums[0], nums[1])
            elif op == 'TD' and len(nums) >= 2:
                st['leading'] = -nums[1]
                next_line(nums[0], nums[1])
            elif op == 'Tm' and len(nums) >= 6:
                tm[:] = nums[:6]
                tlm[:] = tm
            elif op == 'T*':
                next_line(0, -st['leading'])
            elif op == 'Tj' and a and isinstance(a[-1], bytes):
                show([a[-1]])
            elif op == "'" and a and isinstance(a[-1], bytes):
                next_line(0, -st['leading'])
                show([a[-1]])
            elif op == '"' and len(a) >= 3 and isinstance(a[-1], bytes):
                st['tw'], st['tc'] = float(a[0]), float(a[1])
                next_line(0, -st['leading'])
                show([a[-1]])
            elif op == 'TJ' and a and isinstance(a[-1], list):
                show(a[-1])
            elif op == 'Do' and a and depth < 5:
                ref = xobjects.get(a[-1])
                xo = doc.get(ref)
                if isinstance(xo, dict) and xo.get('Subtype') == 'Form' and isinstance(ref, Ref) \
                        and ref.num not in stack_refs and budget[0] > 0:
                    budget[0] -= 1
                    mtx = doc.get(xo.get('Matrix')) or [1, 0, 0, 1, 0, 0]
                    _run(doc, doc.stream_of(ref), xo.get('Resources') or res,
                         _mul([float(doc.get(x)) for x in mtx], ctm), pieces, depth + 1,
                         stack_refs | {ref.num}, budget)
        except (TypeError, ValueError, IndexError, KeyError, ZeroDivisionError):
            continue


def extract_text(data):
    if not data.startswith(b'%PDF'):
        raise ApiError(400, 'That file is not a PDF')
    if re.search(rb'/Encrypt\s*[<\d]', data):
        raise ApiError(400, 'This PDF is password protected. Download an unprotected copy, or a CSV/QFX file.')
    try:
        doc = Doc(data)
        return '\n'.join(_page_text(doc, p, r) for p, r in doc.pages())
    except ApiError:
        raise
    except Exception:
        raise ApiError(400, 'This PDF could not be read. Download the statement as CSV or QFX/OFX instead.')
