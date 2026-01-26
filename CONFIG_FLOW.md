

# FORM: "Scan-flow"

Input:
- Mode
- Barcodes

Submit, for each barcode passed:
-> Process queue

## Process queue

If exists, then continue to process using current mode
-> Process code
If new, then show Map product flow
-> FORM:map_product

## FORM: map_product

Display:
- {barcode}
- {lookup_output}
- {name_suggestions}

Input:
- ´product´ as id / name 
- ("product_mode")
- ´parent_product´ as id / name

Submit:

- If ´parent_product´ is int, then has selected an existing product to use as a parent.
  -> Fetch product, and cache it as ´map_product´
- If ´parent_product´ is str, then should create a parent product to assign ´product´ to.
  Either:
  a. Show FORM:create_parent_product and pre-fill fields from ´product´
  b. Create parent product automatically based on ´product´

- If ´product´ is int, then has selected an existing product to map to.
  -> Fetch product, and cache it as ´map_product´
- If ´product´ is str, then a new product should be created to map to. 
  -> Next form to render is FORM:create_product

- If ´product.parent_product_id´ is not set, then set it as long as ´parent´ is defined
