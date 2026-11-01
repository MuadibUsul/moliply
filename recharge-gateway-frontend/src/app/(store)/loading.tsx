export default function StoreLoading() {
  return (
    <div className="container-page py-16" role="status" aria-live="polite">
      <span className="sr-only">加载中</span>
      <div className="skeleton mx-auto h-8 w-52" />
      <div className="skeleton mx-auto mt-4 h-4 w-72" />
      <div className="mx-auto mt-10 grid max-w-4xl gap-6 md:grid-cols-[1.1fr_0.9fr]">
        <div className="surface h-[420px]" />
        <div className="surface h-[420px]" />
      </div>
    </div>
  );
}
