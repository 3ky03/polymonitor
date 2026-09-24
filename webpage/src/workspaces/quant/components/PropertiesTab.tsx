import type { PropertyGroup } from '../types';

export function PropertiesTab({ groups }: { groups: PropertyGroup[] }) {
  return (
    <div className="qtv-properties">
      {groups.map((group) => (
        <section key={group.title}>
          <h3>{group.title}</h3>
          {group.rows.map((row) => (
            <div key={`${group.title}-${row.label}`}>
              <span>{row.label}</span>
              <strong>{row.value}</strong>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
