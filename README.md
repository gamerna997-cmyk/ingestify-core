# Sortera API Core: Enterprise Data Ingestion & Sanitization Engine

## Overview
Sortera is a high-volume, multi-tenant API gateway that allows applications to ingest, secure, and clean messy incoming data strings via Gemini AI in under 5 milliseconds with built-in rate-limiting.

## Integration Quickstart

### Node.js (JavaScript) Example
```javascript
const fetch = require('node-fetch'); // or use global fetch in modern environments

const enterpriseId = 'your-enterprise-id';
const url = `https://onrender.com/${enterpriseId}`;

const payload = {
  data: 'Raw, untrusted input string from user or external system'
};

fetch(url, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-ENTERPRISE-TOKEN': 'your-secure-access-token'
  },
  body: JSON.stringify(payload)
})
  .then(response => response.json())
  .then(result => console.log('Sanitized output:', result))
  .catch(error => console.error('Error:', error));
```

### Python Example
```python
import requests
import json

enterprise_id = 'your-enterprise-id'
url = f'https://onrender.com/{enterprise_id}'

payload = {
    'data': 'Raw, untrusted input string from user or external system'
}

headers = {
    'Content-Type': 'application/json',
    'X-ENTERPRISE-TOKEN': 'your-secure-access-token'
}

response = requests.post(url, headers=headers, data=json.dumps(payload))
print('Sanitized output:', response.json())
```

## Security Headers
Developers must pass their secure access token inside the request header labeled `X-ENTERPRISE-TOKEN`.