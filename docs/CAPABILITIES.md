# Verified test-server capabilities

The console writes directly to the Tahesab test server using its existing test configuration. The client does not execute Kohan trades or change Kariz data.

## Enabled actions

| Action | State | Verification |
|---|---|---|
| Connection | Enabled | Read-only catalogue request returns rows from `TahesabDB`. |
| Customers | Enabled | Bounded customer lookup, details, and `DoNewMoshtari` creation/readback are verified. Customer code is the stable identity. |
| Complete customer selection | Enabled | `DoListMoshtari` / `DoListMoshtari` with `[""]` returned 1,897 records on 2026-10-11. All creation forms and balances search this complete snapshot by name, mobile, or code and paginate results. Customer-page ranges do not restrict selection. |
| Customer editing | Enabled | `DoEditMoshtari` was exercised on dedicated test customer `1896`; the requested name change was read back and restored. `BDate` was carried forward, and the read-only `City` and `Rasteh` values remained unchanged. The API example uses `-1` for omitted `Moaref` and `Code_Moaref`; Tahesab does not return those fields for readback. |
| Customer and global document history | Enabled | `DoListAsnad` with an empty customer selector and count `0` returned 4,054 source rows on 2026-10-10. Counts 25 and 50 returned recent subsets across multiple customers; sample documents matched customer-specific readbacks. The initial documents page automatically uses this global read, with server-side filtering/pagination and no customer-code scan. Missing customer context is preserved, not inferred from the outer document key. |
| Cash and bank documents | Enabled | Both directions were created and read back by factor, customer, ID/Radif, amount, direction, bank and reference. |
| Coin and bar physical documents | Enabled | Receipt and delivery were verified for the configured Emami coin and Nadir silver bar, including count, weight, direction and balance effects. |
| Gold physical documents | Enabled | Both receipt and delivery were verified for miscellaneous 750 gold and آبشده 750. Their source document mode, direction, weight, purity, and aggregate balance effects matched. Physical receipts have no invented sale price. |
| Coin and bar accounting trades | Enabled | Both directions were verified for the configured Emami coin and Nadir silver bar, including unit price, total, document direction and balance effects. |
| آبشده 750 accounting trades | Enabled | Both directions were verified at per-mesghal and per-gram rates. Tahesab returned `MazanehType=Mesghal` or `Gram`; weight, direction, amount, factor and money/gold balances matched. The configured conversion factor is 4.3318 grams per mesghal. |
| Miscellaneous gold accounting trades | Disabled individually | Tahesab readback does not identify this trade mode. The console cannot prove the document was recorded as miscellaneous gold, so the action stays disabled. |
| Balances | Enabled | Money, aggregate metals, Tahesab’s `مانده منهای سکه` value, and configured coin/bar balances are read directly from the selected customer. The non-coin gold value is combined; it is not labeled as a separate miscellaneous-gold balance. |

All financial creates persist a factor and local operation record before sending. The client verifies through source readback, and an uncertain result can be checked without resending.

## Test data notes

Controlled verification used source customer `1891`. Test receipts, deliveries and trades were paired or compensated so the final balance returned to its prior value. One earlier malformed probe remains visible in the test history with uncertain status; it is not treated as a successful document or resent.

Customer editing verification used dedicated source customer `1896`. A nonempty birthdate was preserved through the edit and restore; source `City` and `Rasteh` readbacks were unchanged because they are not edit tuple positions. The API documentation's example uses `-1` for the omitted referrer arguments, but those fields are not exposed in source readback. Customer `1894` was created during initial field discovery and remains unchanged.

The client API was also exercised with name, contact, address, and national-ID changes on `1896`; source readback verified the changes, and a second operation restored the original values. Reusing the first operation ID made no second upstream write. The journal stores the expected fields and preserved readback fields before sending, so timeout/restart recovery checks those fields without resending.

The catalogue mapping keeps the Persian Kariz display title separate from Tahesab’s source mode. `gold_750` is Tahesab’s miscellaneous physical mode (0); `gold_abshode_750` is the source آبشده mode (1). Tahesab has no separate product ID for these gold modes.
