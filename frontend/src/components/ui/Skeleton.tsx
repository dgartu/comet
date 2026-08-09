interface SkeletonProps {
  label: string;
  lines?: number;
}

export function Skeleton({ label, lines = 3 }: SkeletonProps) {
  return (
    <div aria-busy="true" className="skeleton" role="status">
      <span className="visually-hidden">{label}</span>
      {Array.from({ length: lines }, (_, index) => (
        // Static presentation-only lines have no identity beyond their position.
        // biome-ignore lint/suspicious/noArrayIndexKey: line positions are stable
        <span aria-hidden="true" key={index} />
      ))}
    </div>
  );
}
