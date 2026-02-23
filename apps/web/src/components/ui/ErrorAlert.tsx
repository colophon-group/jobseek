type ErrorAlertProps = {
  message: string;
};

export function ErrorAlert({ message }: ErrorAlertProps) {
  if (!message) return null;
  return (
    <div role="alert" className="mb-4 rounded-md border border-error-border bg-error-bg px-4 py-3 text-sm text-error">
      {message}
    </div>
  );
}
