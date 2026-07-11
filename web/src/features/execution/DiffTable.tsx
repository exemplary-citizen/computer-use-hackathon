import type { FieldChange } from "./api";

/** Field-level before → after table shared by approval and result surfaces. */
export function DiffTable({ changes }: { changes: FieldChange[] }) {
  return (
    <table className="diff-table">
      <thead>
        <tr>
          <th scope="col">Field</th>
          <th scope="col">Before</th>
          <th aria-hidden="true" />
          <th scope="col">After</th>
        </tr>
      </thead>
      <tbody>
        {changes.map((change) => (
          <tr key={change.field}>
            <th scope="row">{change.field}</th>
            <td>{formatValue(change.before)}</td>
            <td aria-hidden="true" className="diff-arrow">
              →
            </td>
            <td>{formatValue(change.after)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
