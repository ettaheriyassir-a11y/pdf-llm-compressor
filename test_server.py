import requests

files = {'file': ('test.pdf', open('test.pdf', 'rb'), 'application/pdf')}
data = {
    'dictCompression': 'true',
    'semanticDeduplication': 'true',
    'minifyXml': 'true',
    'chunkSize': '800'
}

response = requests.post('http://localhost:8000/api/v1/compress', files=files, data=data)
print(response.status_code)
try:
    print(response.json())
except:
    print(response.text)
