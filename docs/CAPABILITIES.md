# Verified test-server capabilities

The console writes directly to the Tahesab test server using its existing test configuration. The client does not execute Kohan trades or change Kariz data.

## Enabled actions

| Action | State | Verification |
|---|---|---|
| Connection | Enabled | Read-only catalogue request returns rows from `TahesabDB`. |
| Customers | Enabled | Bounded customer lookup, details, and `DoNewMoshtari` creation/readback are verified. Customer code is the stable identity. |
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

The catalogue mapping keeps the Persian Kariz display title separate from Tahesab’s source mode. `gold_750` is Tahesab’s miscellaneous physical mode (0); `gold_abshode_750` is the source آبشده mode (1). Tahesab has no separate product ID for these gold modes.
