# Acme Corp — Finance Operations Handbook

These are the rules any person (or AI worker) must follow when doing finance work.

## Systems
- Vendor invoices arrive as PDFs in the shared inbox folder (`company_data/inbox/`).
- All payables are recorded in **Acme Ops** (the internal system). Its address and the
  sandbox login are in `company_data/accounts.md`.

## Recording a vendor invoice
1. Use the invoice number, vendor, total payable amount and due date exactly as printed.
2. Amounts are entered as plain numbers without currency symbols or thousands separators
   (e.g. `124000.00`). Indian digit grouping like `1,24,000.00` means one lakh twenty-four thousand.
3. Dates are entered as `YYYY-MM-DD`. Invoices from some vendors use DD/MM/YYYY.
4. If the due date is not printed but payment terms are (e.g. "Net 30"), compute the due date
   from the invoice date and say so in the Notes field.
5. If neither a due date nor payment terms are printed, **do not guess** — ask the requester.
6. Put the source PDF file name in the Notes field so the record can be audited.

## Controls
- **Approval required:** any invoice with a total above **INR 50,000** must be approved by a
  human before it is saved in Acme Ops.
- **No duplicates:** never record an invoice whose vendor + invoice number already exists.
  Check Acme Ops first. If it exists, report it instead of creating it.
- **Overdue invoices:** an open invoice whose due date is before today should be marked
  `flagged` so the payments team chases it.
- Never delete records. Never change an invoice's amount after it is saved.

## Done means
The record is visible in Acme Ops with the correct values. A success message on screen
is not enough; re-open the record and check it.
