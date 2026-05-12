import tiktoken
enc = tiktoken.get_encoding("cl100k_base")

for ref in ['<r i="1"/>', '<r i="9"/>', '<r i="10"/>', '<r i="99"/>']:
    print(f'{ref!r} = {len(enc.encode(ref))} tokens')

# Simulate dictionary entry cost
for phrase, label in [
    ("The methodology presented in this section", "40-char phrase"),
    ("In the context of the AdS/CFT correspondence, the following result holds for all values", "85-char phrase"),
]:
    t = len(enc.encode(phrase))
    entry_tag = f'<w i="1">{phrase}</w>'
    entry_cost = len(enc.encode(entry_tag))
    print(f'{label}: {t} tokens bare, {entry_cost} tokens as dict entry')
    for c in [2, 3, 5]:
        ref_cost = len(enc.encode('<r i="1"/>'))
        savings = c * t - (entry_cost + c * ref_cost)
        print(f'  {c}x occurrences -> net savings: {savings} tokens')
