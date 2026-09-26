/**
 * client_pdf_builder.js — Pure Client-Side Image-to-PDF Generator
 * Zero external dependencies. Generates standard ISO 32000-1 compliant landscape PDF documents
 * directly in the user's browser in milliseconds.
 */
(function(global) {
  function dataUrlToBytes(dataUrl) {
    const base64 = dataUrl.includes(',') ? dataUrl.split(',')[1] : dataUrl;
    const binary = atob(base64);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  function strToBytes(str) {
    const bytes = new Uint8Array(str.length);
    for (let i = 0; i < str.length; i++) {
      bytes[i] = str.charCodeAt(i) & 0xff;
    }
    return bytes;
  }

  function concatUint8Arrays(arrays) {
    let totalLen = 0;
    for (const a of arrays) totalLen += a.length;
    const out = new Uint8Array(totalLen);
    let offset = 0;
    for (const a of arrays) {
      out.set(a, offset);
      offset += a.length;
    }
    return out;
  }

  /**
   * Generates a PDF Blob from an array of JPEG Data URLs or Uint8Arrays.
   * @param {Array<string|Uint8Array>} jpegList 
   * @param {number} width 
   * @param {number} height 
   * @returns {Blob} application/pdf Blob
   */
  function generatePdfFromJpegList(jpegList, width = 1280, height = 720) {
    const pageCount = jpegList ? jpegList.length : 0;
    if (pageCount === 0) throw new Error("No slide frames provided to generate PDF.");

    const parts = [];
    let currentOffset = 0;
    const objOffsets = {};

    function write(chunk) {
      const bytes = typeof chunk === 'string' ? strToBytes(chunk) : chunk;
      parts.push(bytes);
      const start = currentOffset;
      currentOffset += bytes.length;
      return start;
    }

    // 1. PDF Header
    write("%PDF-1.4\n%\xe2\xe3\xcf\xd3\n");

    // 2. Catalog (Obj 1)
    objOffsets[1] = currentOffset;
    write("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");

    // 3. Pages (Obj 2)
    objOffsets[2] = currentOffset;
    let kids = "";
    for (let i = 0; i < pageCount; i++) {
      kids += `${3 + i * 3} 0 R `;
    }
    write(`2 0 obj\n<< /Type /Pages /Count ${pageCount} /Kids [ ${kids.trim()} ] >>\nendobj\n`);

    // 4. For each page:
    // Page Obj (3 + i*3)
    // Image Obj (3 + i*3 + 1)
    // Content Stream Obj (3 + i*3 + 2)
    for (let i = 0; i < pageCount; i++) {
      const pageNum = 3 + i * 3;
      const imgNum = 3 + i * 3 + 1;
      const contNum = 3 + i * 3 + 2;

      const item = jpegList[i];
      const jpegBytes = typeof item === 'string' ? dataUrlToBytes(item) : (item.data ? dataUrlToBytes(item.data) : item);
      const contentStr = `q ${width} 0 0 ${height} 0 0 cm /Im${i} Do Q\n`;

      // Page obj
      objOffsets[pageNum] = currentOffset;
      write(`${pageNum} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 ${width} ${height} ] /Resources << /XObject << /Im${i} ${imgNum} 0 R >> >> /Contents ${contNum} 0 R >>\nendobj\n`);

      // Image obj
      objOffsets[imgNum] = currentOffset;
      write(`${imgNum} 0 obj\n<< /Type /XObject /Subtype /Image /Width ${width} /Height ${height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpegBytes.length} >>\nstream\n`);
      write(jpegBytes);
      write("\nendstream\nendobj\n");

      // Content obj
      objOffsets[contNum] = currentOffset;
      write(`${contNum} 0 obj\n<< /Length ${contentStr.length} >>\nstream\n${contentStr}endstream\nendobj\n`);
    }

    // 5. xref table
    const startXref = currentOffset;
    const totalObjs = 2 + pageCount * 3;
    write(`xref\n0 ${totalObjs + 1}\n0000000000 65535 f \n`);
    for (let num = 1; num <= totalObjs; num++) {
      const off = String(objOffsets[num]).padStart(10, '0');
      write(`${off} 00000 n \n`);
    }

    // 6. Trailer
    write(`trailer\n<< /Size ${totalObjs + 1} /Root 1 0 R >>\nstartxref\n${startXref}\n%%EOF\n`);

    const fullBytes = concatUint8Arrays(parts);
    return new Blob([fullBytes], { type: "application/pdf" });
  }

  /**
   * Triggers an immediate browser file download for a PDF Blob.
   */
  function downloadPdfBlob(blob, filename = "Lecture_Slides.pdf") {
    const cleanFilename = (filename || "Lecture_Slides.pdf").replace(/[/\\?%*:|"<>]/g, '_');
    const safeName = cleanFilename.endsWith(".pdf") ? cleanFilename : `${cleanFilename}.pdf`;

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = safeName;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 2000);
  }

  global.ClientPdfBuilder = {
    generatePdfFromJpegList,
    downloadPdfBlob
  };
})(typeof window !== 'undefined' ? window : this);
