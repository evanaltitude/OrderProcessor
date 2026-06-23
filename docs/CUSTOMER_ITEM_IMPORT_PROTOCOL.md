# Customer and Item Import Protocol

Use the import API as the writer for downstream customer and item data. The API validates rows, normalizes field names, assigns stable document IDs, archives source rows, stamps `lastImportedAt`, and writes to Cosmos DB.

## Storage Targets

The console customer profile shows the current target metadata beside **Downstream Customer List** and **Item List**.

- Downstream customers: Cosmos container `customers`, partition `/tenantId`
- Items: Cosmos container `items`, partition `/tenantId` + `/customerId`

The selected distributor customer is the `tenantId`. Distributor-level item lists should omit `customerId` and `customerCode`; the API stores those rows under `customerId: "_global"` as the distributor master item catalog. Customer-specific item override lists can still include a downstream `customerCode`, and the API resolves it to the internal downstream customer id used by order processing.

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
  "sourceName": "customers.json",
  "rows": [
    {
      "cust_code": "100022",
      "customer_name": "CHOW HOUND STORES - MASTER A/R",
      "customer_store_number": null,
      "location_address1": "734 28TH STREET SE",
      "location_city": "GRAND RAPIDS",
      "location_state": "MI",
      "location_zip": "49548",
      "phone": "616-452-7877",
      "customer_website": "WWW.CHOWHOUNDPET.COM",
      "customer_email": null
    }
  ]
}
```

Desired customer row fields from the Pet Food Distributors import are `cust_code`, `customer_name`, `customer_store_number`, `location_address1`, `location_city`, `location_state`, `location_zip`, `phone`, `customer_website`, and `customer_email`. Some values may be `null`; the API accepts that. The original source row is preserved in `rawSource.row`.

The API also accepts existing aliases such as `customerCode`, `name`, `storeNumber`, `routeNumber`, `csrEmail`, `csrFolder`, address fields, `customerEmail`, `senderDomains`, and alias fields.

## Item List Payload

```json
{
  "tenantId": "test-customer",
  "sourceName": "itemNumbers.json",
  "rows": [
    {
      "part_code": "100510100",
      "upc_code": "031865BRN4R",
      "alt_parts_combined": [
        { "alt_part": "031865BRN4R" },
        { "alt_part": "10004120" }
      ],
      "part_desc": "Bed-r Nest Kraft Irradiated 4 gram 1600 per case"
    }
  ]
}
```

Desired item row fields from the Pet Food Distributors import are `part_code`, `upc_code`, `alt_parts_combined`, and `part_desc`. `alt_parts_combined` should be an array of objects with `alt_part`, as shown above. The API stores those values in `items.altPartsCombined` and searchable `items.customerItemNumbers`, so order validation can match UPCs, internal part codes, and alternate identifiers.

For customer-specific item override lists only, include `customerCode` at the payload root:

```json
{
  "tenantId": "test-customer",
  "customerCode": "102914",
  "sourceName": "itemNumbers-hollywood-feed.json",
  "rows": []
}
```

The API also accepts existing aliases such as `internalItemNumber`, `description`, `upc`, `customerItemNumbers`, `altPartsCombined`, and `aliases`.

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
