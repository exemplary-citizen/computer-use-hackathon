# Update a CRM lead

## Goal

Update the requested lead's lifecycle status and owner without changing unrelated fields.

## Inputs

- `lead_name`: exact lead name shown in the CRM.
- `lifecycle_status`: new lifecycle status.
- `owner_name`: new lead owner.

## Procedure

1. Open the target CRM desktop application.
2. Find the lead whose visible name exactly matches `lead_name`.
3. Open the lead details and verify the current record identity.
4. Set Lifecycle Status to `lifecycle_status`.
5. Set Owner to `owner_name`.
6. Review the visible values and stop before Save, Commit, or an equivalent persistent action.
7. Report the staged field changes for human approval.
8. Only after approval, re-check the same record and staged values, commit once, and verify visible success.

## Failure behavior

Stop without committing if the lead is missing, duplicated, the requested values are unavailable, or the staged form cannot be verified.
