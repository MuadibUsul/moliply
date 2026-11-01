export default function AdminLoading() {
  return (
    <div className="space-y-4" role="status" aria-live="polite">
      <span className="sr-only">加载中</span>
      <div className="skeleton h-7 w-48" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="skeleton h-24 rounded-2xl" />
        ))}
      </div>
      <div className="skeleton h-64 rounded-2xl" />
    </div>
  );
}
