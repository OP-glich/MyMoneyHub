"""A small offline PDF writer. Embeds local Windows fonts for Unicode receipts.

No browser, network request, third-party package or user profile is needed.
Monetary strings are provided by the exact-money receipt service.
"""
from pathlib import Path
import os
import struct
import zlib


class Font:
    def __init__(self, filename):
        self.data = Path(filename).read_bytes()
        self.tables = {}
        count = self.u16(self.data, 4)
        for i in range(count):
            tag, _, offset, length = struct.unpack_from('>4sIII', self.data, 12+i*16)
            self.tables[tag.decode()] = self.data[offset:offset+length]
        head, hhea = self.tables['head'], self.tables['hhea']
        self.units = self.u16(head, 18)
        self.bbox = struct.unpack_from('>hhhh', head, 36)
        self.ascent, self.descent = struct.unpack_from('>hh', hhea, 4)
        self.metrics = self.u16(hhea, 34)
        cmap = self.tables['cmap']
        subtables = []
        for i in range(self.u16(cmap, 2)):
            platform, encoding, offset = struct.unpack_from('>HHI', cmap, 4+i*8)
            fmt = self.u16(cmap, offset)
            if fmt in (4, 12) and platform in (0, 3):
                subtables.append((fmt, cmap[offset:]))
        self.format, self.cmap = max(subtables, key=lambda item:item[0])
        self.chars = {}

    @staticmethod
    def u16(data, offset):
        return struct.unpack_from('>H', data, offset)[0]

    def glyph(self, character):
        code = ord(character)
        data = self.cmap
        if self.format == 12:
            count = struct.unpack_from('>I', data, 12)[0]
            for i in range(count):
                start, end, glyph = struct.unpack_from('>III', data, 16+i*12)
                if start <= code <= end:
                    return glyph+code-start
            return 0
        count = self.u16(data, 6)//2
        end_offset = 14
        start_offset = 16+2*count
        delta_offset = start_offset+2*count
        range_offset = delta_offset+2*count
        for i in range(count):
            end = self.u16(data, end_offset+2*i)
            start = self.u16(data, start_offset+2*i)
            if start <= code <= end:
                delta = self.u16(data, delta_offset+2*i)
                shift = self.u16(data, range_offset+2*i)
                if not shift:
                    return (code+delta) & 65535
                at = range_offset+2*i+shift+2*(code-start)
                value = self.u16(data, at)
                return (value+delta) & 65535 if value else 0
        return 0

    def width(self, character):
        glyph = self.glyph(character)
        metric = min(glyph, self.metrics-1)
        return self.u16(self.tables['hmtx'], metric*4)*1000/self.units

    def measure(self, value, size):
        return sum(self.width(c) for c in value)*size/1000

    def encode(self, value):
        encoded = []
        for character in value:
            if self.glyph(character) == 0:
                raise ValueError('This receipt contains a character the local PDF font cannot display. Use Print / Save as PDF in your browser for this receipt.')
            if character not in self.chars:
                self.chars[character] = len(self.chars)+1
            encoded.append(f'{self.chars[character]:04X}')
        return ''.join(encoded)


class PDF:
    def __init__(self):
        directory = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
        regular = directory/'segoeui.ttf'
        bold = directory/'seguisb.ttf'
        if not regular.is_file():
            raise ValueError('The Windows Segoe UI font is unavailable. Use Print / Save as PDF in your browser.')
        self.fonts = [Font(regular), Font(bold if bold.is_file() else regular)]
        self.objects = []
        self.pages = []
        self.commands = []
        self.y = 0
        self.new_page()

    def add(self, content):
        if isinstance(content, str): content = content.encode('ascii')
        self.objects.append(content)
        return len(self.objects)

    def stream(self, content, extra=''):
        compressed = zlib.compress(content)
        return self.add(f'<< /Length {len(compressed)} /Filter /FlateDecode {extra} >>\nstream\n'.encode()+compressed+b'\nendstream')

    def new_page(self):
        if self.commands: self.pages.append(self.commands)
        self.commands = []
        self.y = 62

    def rectangle(self, x, top, width, height, rgb):
        self.commands.append(f'{rgb} rg {x} {842-top-height} {width} {height} re f')

    def text(self, value, x, top, size=11, bold=False, color='0.09 0.25 0.23'):
        value = str(value).replace('\r',' ').replace('\n',' ')
        index = 1 if bold else 0
        encoded = self.fonts[index].encode(value)
        self.commands.append(f'BT /F{index} {size} Tf {color} rg 1 0 0 1 {x} {842-top-size} Tm <{encoded}> Tj ET')

    def lines(self, value, maxwidth, size=11, bold=False):
        font = self.fonts[1 if bold else 0]
        output = []
        line = ''
        # Character-level wrapping also handles long names/references without spaces.
        for character in str(value).replace('\r',''):
            if character == '\n':
                output.append(line)
                line = ''
                continue
            if line and font.measure(line+character, size) > maxwidth:
                split = line.rfind(' ')
                if split > len(line)//2:
                    output.append(line[:split])
                    line = line[split+1:]+character
                else:
                    output.append(line)
                    line = character
            else:
                line += character
        if line or not output: output.append(line)
        return output

    def space(self, height):
        if self.y+height > 775:
            self.new_page()
            self.text('OSI LOAN', 52, self.y, 18, True)
            self.y += 42

    def paragraph(self, value, size=11, bold=False, color='0.09 0.25 0.23', width=491):
        for line in self.lines(value, width, size, bold):
            self.space(size+8)
            self.text(line, 52, self.y, size, bold, color)
            self.y += size+6

    def embed_font(self, font, name):
        file_id = self.stream(font.data, '/Length1 '+str(len(font.data)))
        scale = lambda value:round(value*1000/font.units)
        bbox = ' '.join(str(scale(v)) for v in font.bbox)
        descriptor = self.add(f'<< /Type /FontDescriptor /FontName /{name} /Flags 32 /FontBBox [{bbox}] /ItalicAngle 0 /Ascent {scale(font.ascent)} /Descent {scale(font.descent)} /CapHeight {scale(font.ascent)} /StemV 80 /FontFile2 {file_id} 0 R >>')
        glyphs = b'\0\0'+b''.join(struct.pack('>H',font.glyph(c)) for c in font.chars)
        mapping = self.stream(glyphs)
        widths = ' '.join(str(round(font.width(c))) for c in font.chars)
        descendant = self.add(f'<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{name} /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor {descriptor} 0 R /CIDToGIDMap {mapping} 0 R /DW 1000 /W [1 [{widths}]] >>')
        pairs = [f'<{cid:04X}> <{c.encode("utf-16-be").hex().upper()}>' for c,cid in font.chars.items()]
        blocks = []
        for i in range(0,len(pairs),100):
            part = pairs[i:i+100]
            blocks.append(str(len(part))+' beginbfchar\n'+'\n'.join(part)+'\nendbfchar')
        unicode = ('/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /ReceiptUnicode def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange\n'+'\n'.join(blocks)+'\nendcmap CMapName currentdict /CMap defineresource pop end end').encode('ascii')
        unicode_id = self.stream(unicode)
        return self.add(f'<< /Type /Font /Subtype /Type0 /BaseFont /{name} /Encoding /Identity-H /DescendantFonts [{descendant} 0 R] /ToUnicode {unicode_id} 0 R >>')

    def finish(self):
        pages = self.pages+[self.commands]
        catalog = self.add(b'')
        pages_id = self.add(b'')
        fonts = [self.embed_font(font,'SegoeUI'+str(i)) for i,font in enumerate(self.fonts)]
        page_ids = []
        for number, commands in enumerate(pages,1):
            # Page count is known now; glyphs for digits are already used in receipt text.
            content_id = self.stream(('\n'.join(commands)).encode('ascii'))
            resources = ' '.join(f'/F{i} {font} 0 R' for i,font in enumerate(fonts))
            page_ids.append(self.add(f'<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] /Resources << /Font << {resources} >> >> /Contents {content_id} 0 R >>'))
        self.objects[catalog-1] = f'<< /Type /Catalog /Pages {pages_id} 0 R >>'.encode()
        self.objects[pages_id-1] = ('<< /Type /Pages /Count '+str(len(pages))+' /Kids ['+' '.join(str(p)+' 0 R' for p in page_ids)+'] >>').encode()
        out = bytearray(b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n')
        offsets = [0]
        for i,obj in enumerate(self.objects,1):
            offsets.append(len(out))
            out.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
        xref = len(out)
        out.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
        for offset in offsets[1:]: out.extend(f'{offset:010d} 00000 n \n'.encode())
        out.extend(f'trailer << /Size {len(offsets)} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
        return bytes(out)


def make_pdf(data):
    pdf = PDF()
    pdf.text('OSI LOAN',52,54,32,True)
    pdf.text(data['subtitle'],52,102,9,color='0.37 0.48 0.43')
    for index,line in enumerate(pdf.lines(data['reference'],133,11,True)):
        pdf.text(line,410,62+index*16,11,True)
    pdf.rectangle(52,129,491,2,'0.09 0.31 0.28')
    pdf.y=151
    pdf.paragraph(data['status'],10,color='0.37 0.48 0.43')
    pdf.y+=22
    pdf.paragraph('BORROWER',9,color='0.37 0.48 0.43')
    pdf.paragraph(data['borrower'],22,True)
    pdf.y+=6
    pdf.paragraph(data['purpose'],11,color='0.37 0.48 0.43')
    pdf.y+=20
    for i in range(0,len(data['fields']),2):
        pair = data['fields'][i:i+2]
        wrapped = [pdf.lines(value,226,11) for _,value in pair]
        height = 22+max(len(lines) for lines in wrapped)*16+18
        pdf.space(height)
        top = pdf.y
        for col,((label,value),lines) in enumerate(zip(pair,wrapped)):
            x=52+col*254
            pdf.text(label,x,top,9,color='0.40 0.51 0.46')
            for line_number,line in enumerate(lines): pdf.text(line,x,top+21+line_number*16,11)
        pdf.y+=height
    pdf.y+=5
    pdf.space(115)
    pdf.rectangle(52,pdf.y,491,110,'0.92 0.96 0.94')
    pdf.rectangle(52,pdf.y,3,110,'0.09 0.37 0.32')
    pdf.text(data['total_label'],74,pdf.y+18,11)
    pdf.text(data['total'],74,pdf.y+39,29,True)
    pdf.text(data['total_note'],74,pdf.y+86,9,color='0.40 0.51 0.46')
    pdf.y+=137
    pdf.space(75)
    pdf.paragraph('OSI LOAN',10,True)
    pdf.paragraph(data['note'],9,color='0.40 0.51 0.46')
    pdf.paragraph('Clear terms. A clear record.',9,color='0.47 0.56 0.51')
    return pdf.finish()
