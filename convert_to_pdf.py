from markdown_pdf import MarkdownPdf, Section

pdf = MarkdownPdf(toc_level=2)
with open(r"C:\Users\Pilabs\.gemini\antigravity\brain\4ff93fe6-43da-4c8f-83d8-3941406a1786\calibration_ranges_guide.md", "r", encoding="utf-8") as f:
    content = f.read()

pdf.add_section(Section(content))
pdf.save(r"C:\Users\Pilabs\.gemini\antigravity\brain\4ff93fe6-43da-4c8f-83d8-3941406a1786\calibration_ranges_guide.pdf")
print("PDF Saved Successfully")
