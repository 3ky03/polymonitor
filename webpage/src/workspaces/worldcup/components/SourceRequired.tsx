export type SourceRequiredRow = {
  source: string;
  status: string;
  detail: string;
};

const SOURCE_REQUIRED_LABEL = 'SOURCE REQUIRED';

export function SourceRequired({
  title = SOURCE_REQUIRED_LABEL,
  detail,
  rows,
}: {
  title?: string;
  detail: string;
  rows?: SourceRequiredRow[];
}) {
  return (
    <div className="wm-worldcup-source-required">
      <strong>{title}</strong>
      <p>{detail}</p>
      {rows?.length ? (
        <div className="wm-worldcup-source-required-list">
          {rows.map((row) => (
            <span key={`${row.source}-${row.status}`}>
              <b>{row.source}</b>
              <em>{row.status}</em>
              <small>{row.detail}</small>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
