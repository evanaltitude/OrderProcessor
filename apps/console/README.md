# Order Processor Console

Static console UI plus a small Node host for Azure Web App.

Local static preview:

```powershell
Start-Process .\apps\console\index.html
```

Local hosted preview:

```powershell
$env:ORDER_PROCESSOR_API_BASE_URL = "https://<apim-name>.azure-api.net/order-processor"
$env:ORDER_PROCESSOR_APIM_SUBSCRIPTION_KEY = "<apim-subscription-key>"
node .\apps\console\server.js
```

Open `http://localhost:8080`.

Deployment notes:

- The Web App should run `node server.js`.
- App Service Easy Auth should require Microsoft Entra ID login.
- The Node host proxies `/api/*` to APIM and forwards Easy Auth identity headers.
- The dashboard shows Phase 13 observability metrics and order timeline actions.
- `connect@focuseautomate.com` is the bootstrap platform admin.
- `ORDER_PROCESSOR_API_BASE_URL` must point to the APIM API base URL.
- `ORDER_PROCESSOR_APIM_SUBSCRIPTION_KEY` must contain the console APIM subscription key.
