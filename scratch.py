import tiktoken
enc=tiktoken.get_encoding('cl100k_base')
text='<d><pg n="1">Test Document\nThis is a paragraph to test tokens.</pg></d>'
tokens = enc.encode(text)
print(f"Len: {len(tokens)}")
for t in tokens:
    print(f"[{t}]: {enc.decode([t])!r}")
