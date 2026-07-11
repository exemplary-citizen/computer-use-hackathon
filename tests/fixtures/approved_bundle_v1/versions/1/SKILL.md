---
description: Update a lead's lifecycle status and owner in a desktop CRM, with review before the final save.
---

# Update CRM lead

Use the target application and runtime inputs provided by the caller.

1. Locate the exact lead by visible name and verify its record identity.
2. Change only Lifecycle Status and Owner to the requested values.
3. Check the visible staged values and do not activate Save, Commit, Submit, or an equivalent control.
4. Return the record identity, previous values when visible, and proposed values.
5. Wait for explicit approval.
6. After approval, verify the same record and values, commit once, and confirm visible success.

Do not use remembered coordinates or assume that another CRM shares this application's navigation or labels.
