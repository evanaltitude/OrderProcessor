# Customer and Item Import Protocol

Use the import API as the writer for downstream customer and item data. The API validates rows, normalizes field names, assigns stable document IDs, archives source rows, stamps `lastImportedAt`, and writes to Cosmos DB.

## Storage Targets

The console customer profile shows the current target metadata beside **Downstream Customer List** and **Item List**.

- Downstream customers: Cosmos container `customers`, partition `/tenantId`
- Items: Cosmos container `items`, partition `/tenantId` + `/customerId`

The selected distributor customer is the `tenantId`. Item imports can use the downstream `customerCode`; the API resolves it to the internal downstream customer id used by order processing.

## Power Automate Flow Pattern

Call the API through APIM, not Cosmos DB directly.

- Customer list: `POST {apiBaseUrl}/imports/customers`
- Item list: `POST {apiBaseUrl}/imports/items`
- Auth header: `Ocp-Apim-Subscription-Key`
- Content type: `application/json`

Customer lists should run daily. Item lists should run weekly unless a customer needs a different cadence.

## Customer List Payload

```json
{
  "tenantId": "test-customer",
  "sourceName": "customer-list.json",
  "rows": [
    {
      "customerCode": "102914",
      "name": "Hollywood Feed",
      "storeNumber": "123",
      "routeNumber": "400",
      "csrFolder": "CSR Name"
    }
  ]
}
```

Accepted customer fields include `customerCode`, `name`, `storeNumber`, `routeNumber`, `csrEmail`, `csrFolder`, address fields, `customerEmail`, `senderDomains`, and alias fields.

## Item List Payload

```json
{
  "tenantId": "test-customer",
  "customerCode": "102914",
  "sourceName": "item-list.json",
  "rows": [
    {
      "internalItemNumber": "10001",
      "description": "Item description",
      "upc": "000000000000",
      "customerItemNumbers": "HF-10001"
    }
  ]
}
```

Accepted item fields include `internalItemNumber`, `description`, `upc`, `customerItemNumbers`, `altPartsCombined`, and `aliases`.

## File Content Option

Instead of `rows`, flows may send `sourceContent` with `parserModule` set to `csv`, `json`, or `jsonl`.

```json
{
  "tenantId": "test-customer",
  "sourceName": "items.csv",
  "parserModule": "csv",
  "sourceContent": "internalItemNumber,description,upc\n10001,Item description,000000000000"
}
```
