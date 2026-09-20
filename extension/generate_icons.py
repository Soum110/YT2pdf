"""
Pure Python PNG icon generator (No external dependencies, uses built-in zlib + struct).
Creates high-contrast, crisp 16x16, 48x48, and 128x128 icons for the Chrome extension.
"""
import struct
import zlib
import os

def create_png(width, height, rgba_data):
    """Encodes raw RGBA byte buffer to valid PNG format."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    # PNG Signature
    png = b"\x89PNG\r\n\x1a\n"
    # IHDR
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png += chunk(b"IHDR", ihdr)

    # IDAT (Scanlines with filter byte 0)
    raw_scanlines = bytearray()
    for y in range(height):
        raw_scanlines.append(0)  # Filter type: None
        start = y * width * 4
        raw_scanlines.extend(rgba_data[start:start + width * 4])

    compressed = zlib.compress(bytes(raw_scanlines), level=9)
    png += chunk(b"IDAT", compressed)
    # IEND
    png += chunk(b"IEND", b"")
    return png

def render_icon(size):
    """Draws a red squircle with a white PDF sheet symbol."""
    buf = bytearray(size * size * 4)
    
    pad = size * 0.08
    radius = size * 0.22
    
    # Coordinates of page
    px0 = size * 0.28
    py0 = size * 0.22
    px1 = size * 0.72
    py1 = size * 0.78
    cut = (px1 - px0) * 0.35

    for y in range(size):
        for x in range(size):
            idx = (y * size + x) * 4
            
            # 1. Background rounded rectangle
            inside_bg = False
            # Check corners
            dx = max(pad + radius - x, 0, x - (size - pad - radius))
            dy = max(pad + radius - y, 0, y - (size - pad - radius))
            if dx * dx + dy * dy <= radius * radius and (pad <= x < size - pad) and (pad <= y < size - pad):
                inside_bg = True
            elif (pad + radius <= x < size - pad - radius and pad <= y < size - pad) or \
                 (pad + radius <= y < size - pad - radius and pad <= x < size - pad):
                inside_bg = True

            if not inside_bg:
                buf[idx:idx+4] = (0, 0, 0, 0)
                continue

            # Base color: YouTube Red (#FF0000)
            r, g, b, a = 255, 0, 0, 255

            # 2. White PDF Sheet
            if px0 <= x <= px1 and py0 <= y <= py1:
                # Check top-right cut
                if x > px1 - cut and y < py0 + cut:
                    # Diagonal cut
                    if (x - (px1 - cut)) + (y - py0) < cut:
                        r, g, b = 255, 255, 255
                    else:
                        # Fold triangle
                        r, g, b = 210, 210, 210
                else:
                    r, g, b = 255, 255, 255

                # 3. Document text lines
                doc_w = px1 - px0
                doc_h = py1 - py0
                lx0 = px0 + doc_w * 0.2
                lx1 = px1 - doc_w * 0.2
                
                # Title line (Red)
                if size >= 32 and (py0 + doc_h * 0.45 <= y <= py0 + doc_h * 0.52) and (lx0 <= x <= lx1):
                    r, g, b = 255, 0, 0
                # Content line (Dark grey)
                elif size >= 32 and (py0 + doc_h * 0.62 <= y <= py0 + doc_h * 0.68) and (lx0 <= x <= lx0 + (lx1 - lx0) * 0.75):
                    r, g, b = 100, 100, 100
                # Content line 2 (Light grey)
                elif size >= 48 and (py0 + doc_h * 0.76 <= y <= py0 + doc_h * 0.82) and (lx0 <= x <= lx0 + (lx1 - lx0) * 0.5):
                    r, g, b = 160, 160, 160

            buf[idx] = r
            buf[idx+1] = g
            buf[idx+2] = b
            buf[idx+3] = a

    return create_png(size, size, buf)

def main():
    os.makedirs("extension/icons", exist_ok=True)
    for size in [16, 48, 128]:
        png_bytes = render_icon(size)
        path = f"extension/icons/icon-{size}.png"
        with open(path, "wb") as f:
            f.write(png_bytes)
        print(f"Created {path} ({size}x{size}, {len(png_bytes)} bytes)")

if __name__ == "__main__":
    main()
