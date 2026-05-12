from pdf_parser import process_pdf

print("=" * 55)

# Test 1: tiny placeholder (always overhead-dominated)
with open("test.pdf", "rb") as f:
    xml, orig, comp, chunks = process_pdf(f.read())
savings = max(0, round((1 - comp/orig) * 100)) if orig > 0 else 0
print(f"test.pdf (tiny):  orig={orig}, comp={comp}, savings={savings}%")

# Test 2: realistic synthetic
with open("realistic_test.pdf", "rb") as f:
    xml, orig, comp, chunks = process_pdf(f.read())
savings = max(0, round((1 - comp/orig) * 100)) if orig > 0 else 0
print(f"realistic (8pg):  orig={orig}, comp={comp}, savings={savings}%")
print(f"  dict entries used: {xml.count('<r i=')}")
print(f"  <t> body tags left: {xml.count('<t>')}")

print()
print("First 400 chars of XML:")
print(xml[:400])
